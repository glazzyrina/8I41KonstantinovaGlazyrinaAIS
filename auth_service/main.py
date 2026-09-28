import time
from fastapi import FastAPI, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from passlib.context import CryptContext
import jwt

app = FastAPI(title="Auth Service")

users_db = {}

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

JWT_SECRET = "super_secret_key_change_me_in_production"
JWT_ALGORITHM = "HS256"

security_bearer = HTTPBearer()

class UserAuth(BaseModel):
    username: str
    password: str

@app.post("/register", status_code=status.HTTP_201_CREATED)
def register(user: UserAuth):
    if user.username in users_db:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Пользователь с таким именем уже существует"
        )
    users_db[user.username] = pwd_context.hash(user.password)
    return {"message": f"Пользователь {user.username} успешно зарегистрирован"}


@app.post("/login")
def login(user: UserAuth):
    hashed_password = users_db.get(user.username)
    if not hashed_password or not pwd_context.verify(user.password, hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверное имя пользователя или пароль"
        )
    
    payload = {
        "sub": user.username,
        "exp": time.time() + 3600
    }
    
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return {"access_token": token, "token_type": "bearer"}

@app.get("/me")
def get_me(credentials: HTTPAuthorizationCredentials = Depends(security_bearer)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, 
                detail="Невалидный токен"
            )
        return {"username": username, "status": "active"}
    
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Срок действия токена истек"
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Невалидный токен или подпись изменены"
        )