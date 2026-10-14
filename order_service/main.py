import os
from fastapi import FastAPI, Query
from pydantic import BaseModel
import httpx


app = FastAPI(
    title="Order Service",
    version="3.0.0",
    description="Микросервис управления заказами для GraphQL агрегации",
    docs_url="/api/orders/docs",          # Swagger будет доступен по этому пути
    openapi_url="/api/orders/openapi.json" # Путь к схеме
)

INSTANCE_NAME = os.getenv("INSTANCE_NAME", "order-default")
PORT = int(os.getenv("PORT", "8000"))
CONSUL_URL = os.getenv("CONSUL_URL", "http://consul:8500/v1/agent/service/register")
CONSUL_DEREGISTER_URL = f"http://consul:8500/v1/agent/service/deregister/{INSTANCE_NAME}"

# In-memory БД: заказы пользователей ( userId -> список заказов )
# В items лежат только ID товара (product_id) и количество (quantity)
orders_db = {
    "123": [
        {
            "id": "order-101",
            "userId": "123",
            "createdAt": "2026-10-12T12:00:00Z",
            "items": [
                {"product_id": 1, "quantity": 1},  # Laptop
                {"product_id": 3, "quantity": 2}   # Keyboard
            ]
        },
        {
            "id": "order-102",
            "userId": "123",
            "createdAt": "2026-10-12T15:30:00Z",
            "items": [
                {"product_id": 2, "quantity": 1}   # Wireless mouse
            ]
        }
    ]
}

# Авторегистрация в Consul при старте
@app.on_event("startup")
async def startup_event():
    payload = {
        "Name": "order-service",
        "ID": INSTANCE_NAME,
        "Address": INSTANCE_NAME,
        "Port": PORT
    }
    try:
        async with httpx.AsyncClient() as client:
            await client.put(CONSUL_URL, json=payload, timeout=5.0)
    except Exception:
        pass

# Дерегистрация при выключении
@app.on_event("shutdown")
async def shutdown_event():
    try:
        async with httpx.AsyncClient() as client:
            await client.put(CONSUL_DEREGISTER_URL, timeout=5.0)
    except Exception:
        pass

# Эндпоинт получения заказов по userId (то, что будет вызывать Лера)
@app.get("/api/orders")
async def get_orders_by_user(userId: str = Query(..., description="ID of the user to fetch orders for")):
    user_orders = orders_db.get(userId, [])
    return user_orders
