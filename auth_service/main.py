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

app = FastAPI(title="Auth Service", lifespan=lifespan)
users_db = {}
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security_bearer = HTTPBearer()

JWT_SECRET, JWT_ALGORITHM = "super_secret_key", "HS256"

class UserAuth(BaseModel):
    username: str
    password: str

@app.post("/register", status_code=status.HTTP_201_CREATED)
def register(user: UserAuth):
    if user.username in users_db:
        raise HTTPException(status_code=400, detail="User already exists")
    users_db[user.username] = pwd_context.hash(user.password)
    return {"message": "User registered successfully"}

@app.post("/login")
def login(user: UserAuth):
    hashed = users_db.get(user.username)
    if not hashed or not pwd_context.verify(user.password, hashed):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    
    token = jwt.encode({"sub": user.username, "exp": time.time() + 3600}, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return {"access_token": token, "token_type": "bearer"}

@app.get("/me")
def get_me(credentials: HTTPAuthorizationCredentials = Depends(security_bearer)):
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return {"username": payload.get("sub"), "status": "active"}
    except (jwt.ExpiredSignatureError, jwt.PyJWTError):
        raise HTTPException(status_code=401, detail="Invalid token")
