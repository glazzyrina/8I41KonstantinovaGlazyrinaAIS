import uuid
import json
import asyncio
import os
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from contextlib import asynccontextmanager
from pydantic import BaseModel, Field
from fastapi import FastAPI, HTTPException, status, Header, Depends
from aiokafka import AIOKafkaProducer, AIOKafkaConsumer

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
KAFKA_TOPIC = "orders-events"

producer: Optional[AIOKafkaProducer] = None


# ПРОВЕРКА РОЛИ (RBAC) ОТ API GATEWAY

def require_authorized_role(
    x_user_role: Optional[str] = Header(None, alias="X-User-Role"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id")
):
    """Разрешает доступ для любой авторизованной роли (user или admin)."""
    if x_user_role not in ("user", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: создание и управление заказами доступно только ролям user или admin"
        )
    return {"user_id": x_user_id, "role": x_user_role}


# EVENT STORE & REHYDRATION

class Event(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    order_id: str
    event_type: str
    payload: Dict[str, Any]
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    version: int

class EventStore:
    def __init__(self):
        self._storage: Dict[str, List[Event]] = {}

    def save_event(self, order_id: str, event_type: str, payload: Dict[str, Any]) -> Event:
        if order_id not in self._storage:
            self._storage[order_id] = []
        next_version = len(self._storage[order_id]) + 1
        new_event = Event(order_id=order_id, event_type=event_type, payload=payload, version=next_version)
        self._storage[order_id].append(new_event)
        return new_event

    def get_events(self, order_id: str) -> List[Event]:
        return self._storage.get(order_id, [])

event_store = EventStore()

class OrderAggregate:
    def __init__(self, order_id: str):
        self.order_id = order_id
        self.status = None
        self.total_amount = 0.0
        self.items = []

    def apply(self, event: Event):
        if event.event_type == "OrderCreated":
            self.status = "CREATED"
            self.total_amount = event.payload.get("total_amount", 0.0)
            self.items = event.payload.get("items", [])
        elif event.event_type == "OrderPaid":
            self.status = "PAID"
        elif event.event_type == "OrderCancelled":
            self.status = "CANCELLED"

def rehydrate_order(order_id: str) -> OrderAggregate:
    events = event_store.get_events(order_id)
    aggregate = OrderAggregate(order_id)
    for event in events:
        aggregate.apply(event)
    return aggregate


# READ SIDE (CQRS)

class OrderReadModel(BaseModel):
    order_id: str
    status: str
    total_amount: float
    items: List[Dict[str, Any]]
    updated_at: str

class ReadModelStorage:
    def __init__(self):
        self._storage: Dict[str, OrderReadModel] = {}

    def get_by_id(self, order_id: str) -> Optional[OrderReadModel]:
        return self._storage.get(order_id)

    def update_projection(self, event_dict: Dict[str, Any]):
        order_id = event_dict["order_id"]
        event_type = event_dict["event_type"]
        payload = event_dict["payload"]
        timestamp = event_dict["timestamp"]

        if event_type == "OrderCreated":
            self._storage[order_id] = OrderReadModel(
                order_id=order_id, status="CREATED",
                total_amount=payload.get("total_amount", 0.0),
                items=payload.get("items", []), updated_at=timestamp
            )
        elif event_type == "OrderPaid":
            if order_id in self._storage:
                self._storage[order_id].status = "PAID"
                self._storage[order_id].updated_at = timestamp
        elif event_type == "OrderCancelled":
            if order_id in self._storage:
                self._storage[order_id].status = "CANCELLED"
                self._storage[order_id].updated_at = timestamp

read_model_storage = ReadModelStorage()

async def kafka_consumer_worker():
    consumer = None
    while True:
        try:
            consumer = AIOKafkaConsumer(
                KAFKA_TOPIC,
                bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                group_id="order-service-read-group",
                auto_offset_reset="earliest"
            )
            await consumer.start()
            break
        except Exception:
            if consumer:
                await consumer.stop()
            await asyncio.sleep(5)

    try:
        async for message in consumer:
            event_data = json.loads(message.value.decode('utf-8'))
            read_model_storage.update_projection(event_data)
    except asyncio.CancelledError:
        pass
    finally:
        if consumer:
            await consumer.stop()

@asynccontextmanager
async def lifespan(app: FastAPI):
    global producer
    while True:
        try:
            producer = AIOKafkaProducer(bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS)
            await producer.start()
            break
        except Exception:
            await asyncio.sleep(5)
    
    consumer_task = asyncio.create_task(kafka_consumer_worker())
    yield
    if producer:
        await producer.stop()
    consumer_task.cancel()

app = FastAPI(title="Order Service (CQRS/ES + RBAC)", lifespan=lifespan)

async def send_to_kafka(event: Event):
    if producer:
        await producer.send_and_wait(KAFKA_TOPIC, event.json().encode('utf-8'))


# ЭНДПОИНТЫ ЗАКАЗОВ С ПРОВЕРКОЙ RBAC

class CreateOrderRequest(BaseModel):
    items: List[Dict[str, Any]]
    total_amount: float

@app.post("/orders", status_code=status.HTTP_201_CREATED)
async def create_order(
    request: CreateOrderRequest,
    user_info: dict = Depends(require_authorized_role)
):
    order_id = str(uuid.uuid4())
    payload = {
        "items": request.items,
        "total_amount": request.total_amount,
        "created_by": user_info["user_id"]
    }
    event = event_store.save_event(order_id, "OrderCreated", payload)
    await send_to_kafka(event)
    return {"message": "Order creation commanded", "order_id": order_id, "event": event}

@app.post("/orders/{order_id}/pay")
async def pay_order(order_id: str, user_info: dict = Depends(require_authorized_role)):
    aggregate = rehydrate_order(order_id)
    if aggregate.status is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    if aggregate.status == "PAID":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Order is already paid")
    if aggregate.status == "CANCELLED":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot pay for a cancelled order")
    event = event_store.save_event(order_id, "OrderPaid", {})
    await send_to_kafka(event)
    return {"message": "Order payment commanded", "order_id": order_id, "event": event}

@app.post("/orders/{order_id}/cancel")
async def cancel_order(order_id: str, user_info: dict = Depends(require_authorized_role)):
    aggregate = rehydrate_order(order_id)
    if aggregate.status is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    if aggregate.status == "CANCELLED":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Order is already cancelled")
    if aggregate.status == "PAID":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot cancel an already paid order")
    event = event_store.save_event(order_id, "OrderCancelled", {})
    await send_to_kafka(event)
    return {"message": "Order cancellation commanded", "order_id": order_id, "event": event}

@app.get("/orders/{order_id}", response_model=OrderReadModel)
async def get_order(order_id: str, user_info: dict = Depends(require_authorized_role)):
    order_data = read_model_storage.get_by_id(order_id)
    if not order_data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found in Read Model (yet)")
    return order_data
