import os
import sys
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx

app = FastAPI(title="Catalog Service")

# 1. Получаем настройки из переменных окружения
INSTANCE_NAME = os.getenv("INSTANCE_NAME", "catalog-default")
PORT = int(os.getenv("PORT", "8000"))
CONSUL_URL = os.getenv("CONSUL_URL", "http://consul:8500/v1/agent/service/register")
# Для дерегистрации Consul использует эндпоинт /deregister/{service_id}
CONSUL_DEREGISTER_URL = f"http://consul:8500/v1/agent/service/deregister/{INSTANCE_NAME}"

# 2. In-memory хранилище для товаров (простой словарь Python)
products_db = {
    1: {"id": 1, "name": "Laptop", "price": 75000},
    2: {"id": 2, "name": "Wireless mouse", "price": 2500}
}
id_counter = 3

# Модель данных для Pydantic
class Product(BaseModel):
    name: str
    price: float

# --- СОБЫТИЯ СТАРТА И ОСТАНОВКИ (Интеграция с Consul) ---

@app.on_event("startup")
async def startup_event():
    """Авто-регистрация сервиса в Consul при старте контейнера."""
    # Формируем JSON согласно требованиям HTTP API Consul
    payload = {
        "Name": "catalog-service",  # Имя сервиса для Nginx upstream
        "ID": INSTANCE_NAME,         # Уникальный ID инстанса (catalog-1 / catalog-2)
        "Address": INSTANCE_NAME,    # В сети Docker Compose имя контейнера является его адресом
        "Port": PORT
    }
    
    try:
        async with httpx.AsyncClient() as client:
            # Отправляем PUT запрос на регистрацию
            response = await client.put(CONSUL_URL, json=payload, timeout=5.0)
            if response.status_code == 200:
                print(f"[{INSTANCE_NAME}] Успешно зарегистрирован в Consul", flush=True)
            else:
                print(f"[{INSTANCE_NAME}] Ошибка регистрации в Consul: {response.status_code}", flush=True)
    except Exception as e:
        print(f"[{INSTANCE_NAME}] Не удалось связаться с Consul: {e}", flush=True)

@app.on_event("shutdown")
async def shutdown_event():
    """Авто-дерегистрация сервиса в Consul при корректном завершении."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.put(CONSUL_DEREGISTER_URL, timeout=5.0)
            if response.status_code == 200:
                print(f"[{INSTANCE_NAME}] Успешно удален из Consul", flush=True)
            else:
                print(f"[{INSTANCE_NAME}] Ошибка дерегистрации в Consul: {response.status_code}", flush=True)
    except Exception as e:
        print(f"[{INSTANCE_NAME}] Не удалось отправить запрос дерегистрации: {e}", flush=True)

# --- CRUD ЭНДПОИНТЫ ---

@app.get("/products")
async def get_products():
    """Получить список товаров + instance_id (для проверки балансировки)."""
    return {
        "instance_id": INSTANCE_NAME,
        "products": list(products_db.values())
    }

@app.post("/products")
async def create_product(product: Product):
    """Добавить новый товар."""
    global id_counter
    new_product = {
        "id": id_counter,
        "name": product.name,
        "price": product.price
    }
    products_db[id_counter] = new_product
    id_counter += 1
    return new_product

@app.put("/products/{id}")
async def update_product(id: int, updated_product: Product):
    """Обновить существующий товар."""
    if id not in products_db:
        raise HTTPException(status_code=404, detail="Product not found")
    
    products_db[id].update({
        "name": updated_product.name,
        "price": updated_product.price
    })
    return products_db[id]

@app.delete("/products/{id}")
async def delete_product(id: int):
    """Удалить товар."""
    if id not in products_db:
        raise HTTPException(status_code=404, detail="Product not found")
    
    deleted_product = products_db.pop(id)
    return {"message": "Product deleted successfully", "product": deleted_product}
