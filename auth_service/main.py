import os
import time
import uuid
import jwt
import httpx
from typing import Optional, Dict, Any
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from passlib.context import CryptContext

INSTANCE_NAME = os.getenv("INSTANCE_NAME", "auth-service-1")
SERVICE_PORT = int(os.getenv("PORT", "8000"))

# Секрет берем из переменных окружения
JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-docker-compose-secret-key")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_SECONDS = 3600  # 1 час

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with httpx.AsyncClient() as client:
        try:
            payload = {"Name": "auth-service", "ID": INSTANCE_NAME, "Address": "auth", "Port": SERVICE_PORT}
            await client.put("http://consul:8500/v1/agent/service/register", json=payload, timeout=3.0)
        except Exception:
            pass
    yield
    async with httpx.AsyncClient() as client:
        try:
            await client.put(f"http://consul:8500/v1/agent/service/deregister/{INSTANCE_NAME}", timeout=3.0)
        except Exception:
            pass

app = FastAPI(
    title="Auth Service",
    version="4.0.0",
    description="Микросервис аутентификации, хеширования паролей и выпуска JWT-токенов с ролями",
    lifespan=lifespan,
    docs_url="/auth/docs",
    openapi_url="/auth/openapi.json"
)

# Хранилище пользователей в памяти
users_db: Dict[str, Dict[str, Any]] = {}
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security_bearer = HTTPBearer()


# МОДЕЛИ ЗАПРОСОВ (PYDANTIC)

class UserRegister(BaseModel):
    email: str
    password: str
    role: Optional[str] = Field(default="user", description="Роль пользователя: user или admin")

class UserLogin(BaseModel):
    email: str
    password: str


# ЭНДПОИНТЫ АУТЕНТИФИКАЦИИ

@app.post("/auth/register", status_code=status.HTTP_201_CREATED)
def register(user: UserRegister):
    """Регистрация нового пользователя с хешированием пароля (bcrypt)."""
    clean_email = user.email.strip().lower()
    clean_password = user.password.strip()[:72]
    role = (user.role or "user").strip().lower()

    if not clean_email or not clean_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email и пароль обязательны")

    if role not in ("user", "admin"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Допустимые роли: 'user' или 'admin'")

    if clean_email in users_db:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Пользователь с таким email уже существует")

    user_id = f"user-{uuid.uuid4().hex[:8]}"
    hashed_password = pwd_context.hash(clean_password)

    users_db[clean_email] = {
        "user_id": user_id,
        "email": clean_email,
        "hashed_password": hashed_password,
        "role": role
    }

    return {
        "message": "Пользователь успешно зарегистрирован",
        "user_id": user_id,
        "email": clean_email,
        "role": role
    }


@app.post("/auth/login")
def login(user: UserLogin):
    """Аутентификация пользователя и выдача JWT с claims: sub, role, exp."""
    clean_email = user.email.strip().lower()
    clean_password = user.password.strip()[:72]

    user_record = users_db.get(clean_email)
    if not user_record or not pwd_context.verify(clean_password, user_record["hashed_password"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный email или пароль")

    # Формируем обязательные claims
    payload = {
        "sub": user_record["user_id"],
        "role": user_record["role"],
        "exp": int(time.time()) + JWT_EXPIRATION_SECONDS
    }

    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    if isinstance(token, bytes):
        token = token.decode("utf-8")

    return {
        "access_token": token,
        "token_type": "bearer"
    }


@app.get("/auth/me")
def get_me(credentials: HTTPAuthorizationCredentials = Depends(security_bearer)):
    """Проверка текущего токена."""
    try:
        token_str = credentials.credentials
        payload = jwt.decode(token_str, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return {
            "user_id": payload.get("sub"),
            "role": payload.get("role"),
            "exp": payload.get("exp")
        }
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Срок действия токена истек")
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Невалидный токен")
