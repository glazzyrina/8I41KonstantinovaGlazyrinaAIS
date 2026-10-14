import os
import sys
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
import httpx

# ЛАБОРАТОРНАЯ РАБОТА №2: Базовая настройка, Consul и in-memory БД

app = FastAPI(
    title="Catalog Service",
    version="3.0.0",
    description="Микросервис каталога товаров со Swagger UI и пагинацией",
    docs_url="/api/catalog/docs",
    openapi_url="/api/catalog/openapi.json"
)

INSTANCE_NAME = os.getenv("INSTANCE_NAME", "catalog-default")
PORT = int(os.getenv("PORT", "8000"))
CONSUL_URL = os.getenv("CONSUL_URL", "http://consul:8500/v1/agent/service/register")
CONSUL_DEREGISTER_URL = f"http://consul:8500/v1/agent/service/deregister/{INSTANCE_NAME}"

# База данных с товарами под нужными ID 1, 2, 3
products_db = {
    1: {"id": 1, "name": "Laptop", "price": 75000},
    2: {"id": 2, "name": "Wireless mouse", "price": 2500},
    3: {"id": 3, "name": "Keyboard", "price": 4500},
    4: {"id": 4, "name": "Gaming Monitor", "price": 32000},
    5: {"id": 5, "name": "USB-C Hub", "price": 1800}
}
id_counter = 6

class Product(BaseModel):
    name: str = Field(..., description="Name of the product", example="Mechanical Keyboard")
    price: float = Field(..., description="Price of the product", example=5500.0)

@app.on_event("startup")
async def startup_event():
    payload = {
        "Name": "catalog-service",
        "ID": INSTANCE_NAME,
        "Address": INSTANCE_NAME,
        "Port": PORT
    }
    try:
        async with httpx.AsyncClient() as client:
            await client.put(CONSUL_URL, json=payload, timeout=5.0)
    except Exception:
        pass

@app.on_event("shutdown")
async def shutdown_event():
    try:
        async with httpx.AsyncClient() as client:
            await client.put(CONSUL_DEREGISTER_URL, timeout=5.0)
    except Exception:
        pass


# ЛАБОРАТОРНАЯ РАБОТА №3: Новые пути /api/catalog/, Пагинация и ID

@app.get(
    "/api/catalog/products",
    summary="Get product list with pagination",
    description="Returns a paginated list of available products and the active instance ID."
)
async def get_products(
    page: int = Query(1, ge=1, description="Page number (starts from 1)"),
    limit: int = Query(10, ge=1, le=100, description="Number of items per page")
):
    all_products = list(products_db.values())
    
    start = (page - 1) * limit
    end = start + limit
    paginated_products = all_products[start:end]
    
    return {
        "instance_id": INSTANCE_NAME,
        "page": page,
        "limit": limit,
        "total_items": len(all_products),
        "products": paginated_products
    }

@app.get(
    "/api/catalog/products/{id}",
    summary="Get product by ID",
    description="Retrieves the detailed information of a specific product by its unique identifier."
)
async def get_product_by_id(id: int):
    if id not in products_db:
        raise HTTPException(status_code=404, detail="Product not found")
    return products_db[id]

@app.post("/api/catalog/products", summary="Create new product")
async def create_product(product: Product):
    global id_counter
    new_product = {"id": id_counter, "name": product.name, "price": product.price}
    products_db[id_counter] = new_product
    id_counter += 1
    return new_product

@app.put("/api/catalog/products/{id}", summary="Update existing product")
async def update_product(id: int, updated_product: Product):
    if id not in products_db:
        raise HTTPException(status_code=404, detail="Product not found")
    products_db[id].update({"name": updated_product.name, "price": updated_product.price})
    return products_db[id]

@app.delete("/api/catalog/products/{id}", summary="Delete product")
async def delete_product(id: int):
    if id not in products_db:
        raise HTTPException(status_code=404, detail="Product not found")
    deleted_product = products_db.pop(id)
    return {"message": "Product deleted successfully", "product": deleted_product}
