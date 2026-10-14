import os
from typing import List, Dict
import strawberry
from strawberry.fastapi import GraphQLRouter
from strawberry.dataloader import DataLoader
from fastapi import FastAPI
import httpx

# =====================================================================
# ЛАБОРАТОРНАЯ РАБОТА №3: Центральный GraphQL Gateway и шаблоны агрегации
# =====================================================================

# Сначала объявляем базовый тип продукта, чтобы он был доступен остальным
@strawberry.type(description="Подробная информация о товаре из Catalog-service")
class ProductType:
    id: strawberry.ID = strawberry.field(description="Уникальный идентификатор товара")
    name: str = strawberry.field(description="Наименование товара")
    price: float = strawberry.field(description="Стоимость товара")


# Базовая функция-загрузчик для борьбы с проблемой N+1 (Паттерн DataLoader)
async def load_products_by_ids(keys: List[int]) -> List[ProductType]:
    """
    Пакетный загрузчик: принимает список ID товаров, делает ОДИН запрос 
    к REST API Catalog и возвращает сопоставленные данные.
    """
    async with httpx.AsyncClient() as client:
        # Убрали try-except, чтобы в логах Docker сразу увидеть точную ошибку, если она будет
        response = await client.get("http://nginx:80/api/catalog/products?limit=100", timeout=3.0)
        catalog_data = response.json()
        
        # Гарантируем приведение ID к типу int для точного совпадения ключей
        products_map = {int(p["id"]): p for p in catalog_data.get("products", [])}
        
        result = []
        for product_id in keys:
            # Принудительно конвертируем и ключ в int
            search_key = int(product_id)
            if search_key in products_map:
                p = products_map[search_key]
                result.append(ProductType(id=str(p["id"]), name=p["name"], price=p["price"]))
            else:
                result.append(ProductType(id=str(product_id), name=f"Product {product_id} not found in DB", price=0.0))
        return result



product_loader = DataLoader(load_fn=load_products_by_ids)



@strawberry.type(description="Элемент заказа, связывающий количество и объект товара")
class OrderItemType:
    quantity: int = strawberry.field(description="Количество единиц товара в позиции")
    product_id: strawberry.Private[int] # Внутреннее скрытое поле для ID товара

    @strawberry.field(description="Оптимизированный резолвер: использует DataLoader для предотвращения проблемы N+1")
    async def product(self) -> ProductType:
        # Отправляем ID в пакетный загрузчик
        return await product_loader.load(self.product_id)


@strawberry.type(description="Полная агрегированная структура заказа пользователя")
class OrderType:
    id: strawberry.ID = strawberry.field(description="Уникальный идентификатор заказа")
    userId: strawberry.ID = strawberry.field(description="Идентификатор покупателя")
    createdAt: str = strawberry.field(description="Дата и время оформления заказа")
    items: List[OrderItemType] = strawberry.field(description="Список позиций в данном заказе")


@strawberry.type
class Query:
    @strawberry.field(description="Получить список всех заказов конкретного пользователя по его userId")
    async def ordersByUserId(self, userId: strawberry.ID) -> List[OrderType]:
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(f"http://order-service:8000/api/orders?userId={userId}", timeout=3.0)
                if response.status_code != 200:
                    return []
                orders_data = response.json()
            except Exception:
                return []
            
            result = []
            for order in orders_data:
                graphql_items = [
                    OrderItemType(quantity=item["quantity"], product_id=item["product_id"])
                    for item in order["items"]
                ]
                result.append(OrderType(
                    id=strawberry.ID(order["id"]),
                    userId=strawberry.ID(order["userId"]),
                    createdAt=order["createdAt"],
                    items=graphql_items
                ))
            return result

schema = strawberry.Schema(query=Query)
graphql_app = GraphQLRouter(schema)

app = FastAPI(
    title="Order Service",
    version="3.0.0",
    description="Микросервис управления заказами для GraphQL агрегации"
)
app.include_router(graphql_app, prefix="/graphql")
