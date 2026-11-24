import os
import time
import itertools
from typing import Dict, List, Tuple
import jwt
import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

# Секретный ключ из переменных окружения
JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-docker-compose-secret-key")
JWT_ALGORITHM = "HS256"

# Настройки Rate Limiting
WINDOW_SECONDS = 15 * 60  # 15 минут
GENERAL_LIMIT = 100       # 100 запросов с одного IP
LOGIN_LIMIT = 5           # 5 попыток входа с одного IP

# Хранилище счетчиков запросов в памяти: { (client_ip, bucket): [timestamps] }
rate_limit_store: Dict[Tuple[str, str], List[float]] = {}

# Адреса внутренних микросервисов
AUTH_SERVICE_URL = "http://auth:8000"
CATALOG_INSTANCES = itertools.cycle(["http://catalog-1:8000", "http://catalog-2:8000"])
ORDER_SERVICE_URL = "http://order-service:8000"

app = FastAPI(
    title="API Gateway",
    version="1.0.0",
    description="Центральный шлюз безопасности: JWT-аутентификация, Rate Limiting и проксирование"
)


# MIDDLEWARE 1: RATE LIMITING

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_ip = request.client.host if request.client else "unknown"
    path = request.url.path
    now = time.time()

    # Определяем корзину лимитов: строгий для логина, общий для остальных путей
    if path.endswith("/auth/login") and request.method == "POST":
        bucket = "login"
        max_requests = LOGIN_LIMIT
    else:
        bucket = "general"
        max_requests = GENERAL_LIMIT

    key = (client_ip, bucket)
    timestamps = rate_limit_store.get(key, [])

    # Очищаем устаревшие запросы, вышедшие за окно 15 минут
    timestamps = [ts for ts in timestamps if now - ts < WINDOW_SECONDS]

    # Вычисляем время сброса окна (Unix timestamp)
    reset_time = int(timestamps[0] + WINDOW_SECONDS) if timestamps else int(now + WINDOW_SECONDS)

    if len(timestamps) >= max_requests:
        headers = {
            "X-RateLimit-Limit": str(max_requests),
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": str(reset_time)
        }
        return JSONResponse(
            status_code=429,
            content={"detail": "Too Many Requests: превышен лимит запросов"},
            headers=headers
        )

    # Фиксируем текущий запрос
    timestamps.append(now)
    rate_limit_store[key] = timestamps
    remaining = max(0, max_requests - len(timestamps))

    # Передаем управление дальше (в JWT middleware и роутер)
    response: Response = await call_next(request)

    # Добавляем обязательные заголовки X-RateLimit-* в каждый ответ
    response.headers["X-RateLimit-Limit"] = str(max_requests)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_time)

    return response


# ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ВАЛИДАЦИИ JWT

def validate_jwt_and_extract_headers(request: Request) -> Dict[str, str]:
    """Проверяет заголовок Authorization: Bearer <token> и возвращает X-User-Id и X-User-Role."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise ValueError("Отсутствует или неверный формат заголовка Authorization: Bearer <token>")

    token = auth_header.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise ValueError("Срок действия JWT истек (Token expired)")
    except jwt.PyJWTError:
        raise ValueError("Невалидная подпись или формат JWT")

    user_id = payload.get("sub")
    role = payload.get("role")
    if not user_id or not role:
        raise ValueError("В токене отсутствуют обязательные claims (sub, role)")

    return {
        "X-User-Id": str(user_id),
        "X-User-Role": str(role)
    }


# ПРОКСИРОВАНИЕ ЗАПРОСОВ ВО ВНУТРЕННИЕ СЕРВИСЫ

async def forward_request(request: Request, target_base_url: str, target_path: str, extra_headers: Dict[str, str] = None) -> Response:
    """Пересылает HTTP-запрос во внутренний сервис и возвращает ответ клиенту."""
    url = f"{target_base_url}{target_path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"

    # Копируем безопасные заголовки клиента
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length", "x-user-id", "x-user-role")
    }
    if extra_headers:
        headers.update(extra_headers)

    body = await request.body()

    async with httpx.AsyncClient() as client:
        try:
            proxy_res = await client.request(
                method=request.method,
                url=url,
                headers=headers,
                content=body,
                timeout=10.0
            )
            excluded_headers = {"content-encoding", "content-length", "transfer-encoding", "connection"}
            res_headers = {k: v for k, v in proxy_res.headers.items() if k.lower() not in excluded_headers}
            return Response(content=proxy_res.content, status_code=proxy_res.status_code, headers=res_headers)
        except httpx.RequestError as exc:
            return JSONResponse(status_code=502, content={"detail": f"Ошибка связи с внутренним сервисом: {exc}"})


# Публичные маршруты аутентификации 
@app.api_route("/auth/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def proxy_auth(path: str, request: Request):
    return await forward_request(request, AUTH_SERVICE_URL, f"/auth/{path}")


# Защищенные маршруты каталога 
@app.api_route("/api/catalog/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def proxy_catalog(path: str, request: Request):
    # Разрешаем просмотр Swagger без токена при необходимости, но все API-маршруты строго по JWT
    if path in ("docs", "openapi.json"):
        target_instance = next(CATALOG_INSTANCES)
        return await forward_request(request, target_instance, f"/api/catalog/{path}")

    try:
        user_headers = validate_jwt_and_extract_headers(request)
    except ValueError as err:
        return JSONResponse(status_code=401, content={"detail": str(err)})

    target_instance = next(CATALOG_INSTANCES)
    return await forward_request(request, target_instance, f"/api/catalog/{path}", extra_headers=user_headers)


# Защищенные маршруты заказов
@app.api_route("/orders{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def proxy_orders(path: str, request: Request):
    if path in ("/docs", "/openapi.json"):
        return await forward_request(request, ORDER_SERVICE_URL, f"/orders{path}")

    try:
        user_headers = validate_jwt_and_extract_headers(request)
    except ValueError as err:
        return JSONResponse(status_code=401, content={"detail": str(err)})

    return await forward_request(request, ORDER_SERVICE_URL, f"/orders{path}", extra_headers=user_headers)
