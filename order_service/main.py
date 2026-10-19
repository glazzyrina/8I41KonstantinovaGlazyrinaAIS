import os
import json
import time
import pika
from fastapi import FastAPI, Query
from pydantic import BaseModel
from typing import List

app = FastAPI(
    title="Order Service",
    version="4.0.0",
    description="Микросервис управления заказами с поддержкой паттерна Saga Choreography",
    docs_url="/api/orders/docs",
    openapi_url="/api/orders/openapi.json"
)

# In-memory БД: заказы пользователей ( userId -> список заказов )
orders_db = {
    "123": [
        {
            "id": "order-101",
            "userId": "123",
            "createdAt": "2026-10-12T12:00:00Z",
            "status": "CONFIRMED",
            "totalAmount": 80000.0,
            "items": [
                {"product_id": 1, "quantity": 1},
                {"product_id": 3, "quantity": 2}
            ]
        }
    ]
}

class OrderItemCreate(BaseModel):
    product_id: int
    quantity: int

class OrderCreate(BaseModel):
    userId: str
    items: List[OrderItemCreate]
    totalAmount: float

def process_payment_responses(ch, method, properties, body):
    """Слушатель ответов от Payment: меняет статус заказа."""
    try:
        data = json.loads(body)
        order_id = data.get("orderId")
        routing_key = method.routing_key
        
        target_order = None
        for user_id, user_orders in orders_db.items():
            for order in user_orders:
                if order.get("id") == order_id:
                    target_order = order
                    break
        
        if not target_order or target_order.get("status") in ["CONFIRMED", "CANCELLED"]:
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        if routing_key == 'payment_completed_queue':
            target_order["status"] = "CONFIRMED"
            print(f"[Order Service] Заказ {order_id} УСПЕШНО подтвержден (CONFIRMED).", flush=True)
        elif routing_key == 'payment_failed_queue':
            target_order["status"] = "CANCELLED"
            reason = data.get("reason", "Неизвестная ошибка")
            print(f"[Order Service] КОМПЕНСАЦИЯ: Заказ {order_id} ОТМЕНЕН (CANCELLED). Причина: {reason}", flush=True)
            
            # Отправка события отмены
            try:
                connection = pika.BlockingConnection(pika.ConnectionParameters(host='rabbitmq'))
                channel = connection.channel()
                channel.queue_declare(queue='order_cancelled_queue', durable=True)
                channel.basic_publish(
                    exchange='',
                    routing_key='order_cancelled_queue',
                    body=json.dumps({"orderId": order_id, "reason": reason}),
                    properties=pika.BasicProperties(delivery_mode=2)
                )
                connection.close()
            except Exception:
                pass

        ch.basic_ack(delivery_tag=method.delivery_tag)
    except Exception as e:
        print(f"[Order Service] Ошибка обработки ответа: {e}", flush=True)

def start_order_rabbitmq_consumer():
    """Фоновый запуск прослушивания очередей ответов брокера с переподключением."""
    # Ждём пару секунд, пока брокер RabbitMQ точно начнёт принимать соединения
    time.sleep(5.0)
    while True:
        try:
            connection = pika.BlockingConnection(pika.ConnectionParameters(host='rabbitmq', heartbeat=600))
            channel = connection.channel()
            channel.queue_declare(queue='payment_completed_queue', durable=True)
            channel.queue_declare(queue='payment_failed_queue', durable=True)
            channel.basic_consume(queue='payment_completed_queue', on_message_callback=process_payment_responses)
            channel.basic_consume(queue='payment_failed_queue', on_message_callback=process_payment_responses)
            print("[Order Service] Успешно запущен фоновый мониторинг очередей RabbitMQ.", flush=True)
            channel.start_consuming()
        except Exception as e:
            print(f"[Order Service] Ошибка брокера, переподключение через 5 сек... ({e})", flush=True)
            time.sleep(5.0)

@app.on_event("startup")
async def startup_event():
    import threading
    threading.Thread(target=start_order_rabbitmq_consumer, daemon=True).start()

@app.get("/api/orders")
async def get_orders_by_user(userId: str = Query(..., description="ID пользователя")):
    return orders_db.get(userId, [])

@app.post("/api/orders")
async def create_order(order_data: OrderCreate):
    """Создание заказа со статусом PENDING и публикация OrderCreated."""
    order_id = f"order-{int(time.time())}"
    new_order = {
        "id": order_id,
        "userId": order_data.userId,
        "createdAt": "2026-10-12T10:00:00Z",
        "status": "PENDING",
        "totalAmount": order_data.totalAmount,
        "items": [{"product_id": item.product_id, "quantity": item.quantity} for item in order_data.items]
    }
    
    if order_data.userId not in orders_db:
        orders_db[order_data.userId] = []
    orders_db[order_data.userId].append(new_order)
    
    try:
        connection = pika.BlockingConnection(pika.ConnectionParameters(host='rabbitmq'))
        channel = connection.channel()
        channel.queue_declare(queue='order_created_queue', durable=True)
        channel.basic_publish(
            exchange='',
            routing_key='order_created_queue',
            body=json.dumps(new_order),
            properties=pika.BasicProperties(delivery_mode=2)
        )
        connection.close()
        print(f"[Order Service] Событие OrderCreated успешно отправлено для заказа: {order_id}", flush=True)
    except Exception as e:
        print(f"[Order Service] Ошибка публикации события в RabbitMQ: {e}", flush=True)
        
    return new_order
