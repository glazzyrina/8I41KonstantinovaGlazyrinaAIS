import asyncio
import os
from typing import List, Any
from fastapi import FastAPI
import httpx
import strawberry
from strawberry.fastapi import GraphQLRouter
from strawberry.dataloader import DataLoader

# Внутри сети Docker контейнеры общаются по своим именам
CATALOG_SERVICE_URL = "http://catalog-1:8000/api/catalog/products"
ORDER_SERVICE_URL = "http://order-service:8000/api/orders"

# ==========================================
# ШАГ 4: DATALOADER (Решение проблемы N+1)
# ==========================================
async def batch_load_products(product_ids: List[str]) -> List[Any]:
    async with httpx.AsyncClient() as client:
        tasks = []
        for p_id in product_ids:
            url = f"{CATALOG_SERVICE_URL}/{p_id}"
            tasks.append(client.get(url))
        
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        
        results = []
        for response in responses:
            if isinstance(response, httpx.Response) and response.status_code == 200:
                data = response.json()
                results.append(Product(
                    id=str(data["id"]),
                    name=data["name"],
                    price=float(data["price"])
                ))
            else:
                results.append(None)
        return results

product_loader = DataLoader(load_fn=batch_load_products)

# ==========================================
# ШАГ 3: GraphQL СХЕМА И ИНТРОСПЕКЦИЯ
# ==========================================
@strawberry.type(description="Информация о конкретном товаре из каталога")
class Product:
    id: strawberry.ID = strawberry.field(description="Уникальный идентификатор товара")
    name: str = strawberry.field(description="Название товара")
    price: float = strawberry.field(description="Стоимость товара в валюте магазина")

@strawberry.type(description="Элемент заказа, содержащий товар и его количество")
class OrderItem:
    quantity: int = strawberry.field(description="Количество единиц товара в заказе")

    @strawberry.field(description="Детальная информация о товаре (подгружается через DataLoader без N+1)")
    async def product(self, root) -> Product:
        # Учитываем имя поля root.product_id из базы order_service
        return await product_loader.load(str(root.product_id))

@strawberry.type(description="Информация о заказе пользователя")
class Order:
    id: strawberry.ID = strawberry.field(description="Уникальный идентификатор заказа")
    userId: strawberry.ID = strawberry.field(description="Идентификатор пользователя, создавшего заказ")
    createdAt: str = strawberry.field(description="Дата и время создания заказа")
    items: List[OrderItem] = strawberry.field(description="Список товаров в данном заказе")

class RawOrderItem:
    def __init__(self, product_id: int, quantity: int):
        self.product_id = product_id
        self.quantity = quantity

# ==========================================
# РЕЗОЛВЕРЫ И АГРЕГАЦИЯ ДАННЫХ
# ==========================================
@strawberry.type
class Query:
    @strawberry.field(description="Получить все заказы конкретного пользователя по его ID")
    async def orders_by_user_id(self, user_id: strawberry.ID) -> List[Order]:
        async with httpx.AsyncClient() as client:
            response = await client.get(ORDER_SERVICE_URL, params={"userId": user_id})
            
            if response.status_code != 200:
                return []
            
            orders_data = response.json()
            
            formatted_orders = []
            for order in orders_data:
                raw_items = []
                for item in order.get("items", []):
                    raw_items.append(
                        RawOrderItem(
                            product_id=item["product_id"], # берем из order_service
                            quantity=item["quantity"]
                        )
                    )
                
                formatted_orders.append(
                    Order(
                        id=str(order["id"]),
                        userId=str(order["userId"]),
                        createdAt=order["createdAt"],
                        items=raw_items
                    )
                )
            return formatted_orders

# Инициализация схемы и роутера
schema = strawberry.Schema(query=Query)
graphql_router = GraphQLRouter(schema, graphiql=True)

app = FastAPI(title="GraphQL Gateway")
app.include_router(graphql_router, prefix="/graphql")
