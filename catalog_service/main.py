import os
from typing import Optional
from fastapi import FastAPI, HTTPException, Query, Header, Depends
from fastapi.security import HTTPBearer
from pydantic import BaseModel, Field
import httpx

# Схема для отображения кнопки Authorize в Swagger UI (сам токен проверяет API Gateway)
swagger_bearer = HTTPBearer(auto_error=False)

app = FastAPI(
    title="Catalog Service",
    version="4.0.0",
    description="Микросервис каталога товаров с RBAC-авторизацией по заголовкам от API Gateway",
    docs_url="/api/catalog/docs",
    openapi_url="/api/catalog/openapi.json",
    dependencies=[Depends(swagger_bearer)]
)

INSTANCE_NAME = os.getenv("INSTANCE_NAME", "catalog-default")
PORT = int(os.getenv("PORT", "8000"))
CONSUL_URL = os.getenv("CONSUL_URL", "http://consul:8500/v1/agent/service/register")
CONSUL_DEREGISTER_URL = f"http://consul:8500/v1/agent/service/deregister/{INSTANCE_NAME}"

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


# ПРОВЕРКА РОЛЕЙ (RBAC) ЧЕРЕЗ ЗАГОЛОВКИ GATEWAY

def require_authenticated(x_user_role: Optional[str] = Header(None, alias="X-User-Role")):
    """Проверка, что запрос пришел от авторизованного пользователя (user или admin)."""
    if x_user_role not in ("user", "admin"):
        raise HTTPException(
            status_code=403,
            detail="Forbidden: требуется валидная роль пользователя (user или admin)"
        )
    return x_user_role

def require_admin(x_user_role: Optional[str] = Header(None, alias="X-User-Role")):
    """Проверка, что роль пользователя строго admin (для создания/удаления товаров)."""
    if x_user_role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Forbidden: доступ разрешен только для роли admin"
        )
    return x_user_role


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

# ЭНДПОИНТЫ КАТАЛОГА С RBAC

@app.get("/api/catalog/products", summary="Get product list with pagination")
async def get_products(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    role: str = Depends(require_authenticated)
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

@app.get("/api/catalog/products/{id}", summary="Get product by ID")
async def get_product_by_id(id: int, role: str = Depends(require_authenticated)):
    if id not in products_db:
        raise HTTPException(status_code=404, detail="Product not found")
    return products_db[id]

@app.post("/api/catalog/products", summary="Create new product (Admin only)")
async def create_product(product: Product, role: str = Depends(require_admin)):
    global id_counter
    new_product = {"id": id_counter, "name": product.name, "price": product.price}
    products_db[id_counter] = new_product
    id_counter += 1
    return new_product

@app.put("/api/catalog/products/{id}", summary="Update existing product (Admin only)")
async def update_product(id: int, updated_product: Product, role: str = Depends(require_admin)):
    if id not in products_db:
        raise HTTPException(status_code=404, detail="Product not found")
    products_db[id].update({"name": updated_product.name, "price": updated_product.price})
    return products_db[id]

@app.delete("/api/catalog/products/{id}", summary="Delete product (Admin only)")
async def delete_product(id: int, role: str = Depends(require_admin)):
    if id not in products_db:
        raise HTTPException(status_code=404, detail="Product not found")
    deleted_product = products_db.pop(id)
    return {"message": "Product deleted successfully", "product": deleted_product}