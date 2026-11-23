import json
import asyncio
import os
from contextlib import asynccontextmanager
from typing import Dict, Any
from fastapi import FastAPI
from aiokafka import AIOKafkaConsumer

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
KAFKA_TOPIC = "orders-events"

class AnalyticsStorage:
    def __init__(self):
        self.total_orders_count = 0
        self.total_paid_amount = 0.0
        self.avg_order_amount = 0.0
        self.status_counts = {"CREATED": 0, "PAID": 0, "CANCELLED": 0}
        self._order_amounts: Dict[str, float] = {}

    def process_event(self, event_dict: Dict[str, Any]):
        event_type = event_dict.get("event_type")
        order_id = event_dict.get("order_id")
        payload = event_dict.get("payload", {})

        if event_type == "OrderCreated":
            order_amount = payload.get("total_amount", 0.0)
            self._order_amounts[order_id] = order_amount
            self.total_orders_count += 1
            self.status_counts["CREATED"] += 1
            all_amounts = self._order_amounts.values()
            self.avg_order_amount = sum(all_amounts) / len(all_amounts) if all_amounts else 0.0
        elif event_type == "OrderPaid":
            if order_id in self._order_amounts:
                if self.status_counts["CREATED"] > 0:
                    self.status_counts["CREATED"] -= 1
                self.status_counts["PAID"] += 1
                self.total_paid_amount += self._order_amounts[order_id]
        elif event_type == "OrderCancelled":
            if order_id in self._order_amounts:
                self.status_counts["CANCELLED"] += 1
                if self.status_counts["CREATED"] > 0:
                    self.status_counts["CREATED"] -= 1

analytics_storage = AnalyticsStorage()

async def kafka_analytics_consumer_worker():
    consumer = None
    while True:
        try:
            consumer = AIOKafkaConsumer(
                KAFKA_TOPIC,
                bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                group_id="analytics-service-group",
                auto_offset_reset="earliest"
            )
            await consumer.start()
            print("Successfully connected Analytics Consumer to Kafka")
            break
        except Exception as e:
            print(f"Analytics waiting for Kafka network... ({e})")
            if consumer:
                await consumer.stop()
            await asyncio.sleep(5)

    try:
        async for message in consumer:
            event_data = json.loads(message.value.decode('utf-8'))
            analytics_storage.process_event(event_data)
    except asyncio.CancelledError:
        pass
    finally:
        if consumer:
            await consumer.stop()

@asynccontextmanager
async def lifespan(app: FastAPI):
    consumer_task = asyncio.create_task(kafka_analytics_consumer_worker())
    yield
    consumer_task.cancel()

app = FastAPI(title="Analytics Service (CQRS/ES)", lifespan=lifespan)

@app.get("/analytics/summary")
async def get_analytics_summary():
    return {
        "total_orders": analytics_storage.total_orders_count,
        "total_paid_amount": analytics_storage.total_paid_amount,
        "avg_order_amount": round(analytics_storage.avg_order_amount, 2),
        "status_distribution": analytics_storage.status_counts
    }
