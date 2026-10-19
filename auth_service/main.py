import os, time, jwt, httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from passlib.context import CryptContext

INSTANCE_NAME = os.getenv("INSTANCE_NAME", "auth-service-1")
SERVICE_PORT = int(os.getenv("PORT", "8000"))

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Регистрация при старте
    async with httpx.AsyncClient() as client:
        try:
            payload = {"Name": "auth-service", "ID": INSTANCE_NAME, "Address": "auth", "Port": SERVICE_PORT}
            await client.put("http://consul:8500/v1/agent/service/register", json=payload, timeout=3.0)
        except Exception:
            pass # Если упадет, Docker-compose позволит приложению работать
            
    yield
    
    # Дерегистрация при выключении
    async with httpx.AsyncClient() as client:
        try:
            await client.put(f"http://consul:8500/v1/agent/service/deregister/{INSTANCE_NAME}", timeout=3.0)
        except Exception:
            pass

app = FastAPI(
    title="Auth Service", 
    version="3.0.0",
    description="Микросервис управления пользователями и выпуска JWT-токенов с OpenAPI",
    lifespan=lifespan,
    docs_url="/api/auth/docs",          # Swagger будет доступен по этому пути
    openapi_url="/api/auth/openapi.json" # Путь к схеме
)
users_db = {}
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security_bearer = HTTPBearer()

JWT_SECRET, JWT_ALGORITHM = "super_secret_key", "HS256"

class UserAuth(BaseModel):
    username: str
    password: str

from typing import Optional

@app.post("/register", status_code=status.HTTP_201_CREATED)
def register(user: Optional[UserAuth] = None, username: Optional[str] = None, password: Optional[str] = None):
    global users_db
    
    # 1. Определяем, откуда пришли данные (из JSON или из адресной строки)
    reg_username = user.username if user else username
    reg_password = user.password if user else password
    
    # 2. Если данных вообще нет, выдаем ошибку
    if not reg_username or not reg_password:
        raise HTTPException(status_code=400, detail="Missing username or password")
        
    # 3. Полностью очищаем строки от системного мусора Windows
    clean_username = str(reg_username).replace('\\', '').replace('"', '').strip()
    clean_password = str(reg_password).replace('\\', '').replace('"', '').strip()
    safe_password = clean_password[:72]
    
    if clean_username in users_db:
        raise HTTPException(status_code=400, detail="Пользователь уже существует")
    
    users_db[clean_username] = pwd_context.hash(safe_password)
    return {"message": "User registered successfully", "username": clean_username}



@app.post("/login")
def login(user: UserAuth):
    clean_username = user.username.replace('\\', '').replace('"', '').strip()
    clean_password = user.password.replace('\\', '').replace('"', '').strip()
    safe_password = clean_password[:72]
    
    hashed = users_db.get(clean_username)
    if not hashed or not pwd_context.verify(safe_password, hashed):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    
    token = jwt.encode({"sub": clean_username, "exp": time.time() + 3600}, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return {"access_token": token, "token_type": "bearer"}


@app.get("/me")
def get_me(credentials: HTTPAuthorizationCredentials = Depends(security_bearer)):
    try:
        # Для библиотек jwt в Python декодирование может возвращать строку или словарь, приводим к стандарту PyJWT
        token_str = credentials.credentials
        if isinstance(token_str, bytes):
            token_str = token_str.decode('utf-8')
            
        payload = jwt.decode(token_str, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return {"username": payload.get("sub")}
    except (jwt.ExpiredSignatureError, jwt.PyJWTError):
        raise HTTPException(status_code=401, detail="Authorization header is required") 
