import os
import time
import grpc
import pybreaker
from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from typing import List

# Импортируем сгенерированные компилятором gRPC файлы
import payment_pb2
import payment_pb2_grpc

app = FastAPI(
    title="Order Service",
    version="5.0.0",
    description="Микросервис управления заказами с интеграцией gRPC и Circuit Breaker",
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
            "status": "SUCCESS",
            "totalAmount": 1500.0,
            "items": [{"product_id": 1, "quantity": 1}]
        }
    ]
}

# НАСТРОЙКА CIRCUIT BREAKER
class LogCircuitBreakerListener(pybreaker.CircuitBreakerListener):
    """Слушатель событий предохранителя для наглядного вывода смены состояний в логи."""
    def state_change(self, cb, old_state, new_state):
        print(f"\n[CIRCUIT BREAKER] >>> СМЕНА СОСТОЯНИЯ: {old_state.name} -> {new_state.name} <<<\n", flush=True)

# Инициализируем предохранитель по требованиям методички:
db_breaker = pybreaker.CircuitBreaker(
    fail_max=3,                  # Порог ошибок: после 3 неудач подряд цепь размыкается (OPEN)
    reset_timeout=10.0,          # Время нахождения в OPEN перед переходом в HALF-OPEN
    listeners=[LogCircuitBreakerListener()]
)

# --- СХЕМЫ ВАЛИДАЦИИ PYDANTIC ---
class OrderItemCreate(BaseModel):
    product_id: int
    quantity: int

class OrderCreate(BaseModel):
    userId: str
    items: List[OrderItemCreate]
    totalAmount: float
    simulate_failure: bool = False # Флаг для контролируемой симуляции ошибки


# FALLBACK ФУНКЦИЯ
def payment_fallback(order_id: str, reason: str):
    """Возвращает осмысленный локальный ответ-заглушку, когда платежная система недоступна."""
    print(f"[Fallback] Вызвана резервная логика для заказа {order_id}. Причина: {reason}", flush=True)
    return {
        "id": order_id,
        "status": "PROCESSING",
        "message": "Платёжная система временно недоступна. Платёж переведён в фоновую обработку."
    }


# REST API ЭНДПОИНТЫ

@app.get("/api/orders")
async def get_orders_by_user(userId: str = Query(..., description="ID пользователя")):
    return orders_db.get(userId, [])

@app.post("/api/orders")
async def create_order(order_data: OrderCreate):
    """Создание заказа и вызов gRPC сервиса оплаты через Circuit Breaker."""
    order_id = f"order-{int(time.time())}"
    
    # Формируем структуру нового заказа со стартовым статусом PENDING
    new_order = {
        "id": order_id,
        "userId": order_data.userId,
        "createdAt": "2026-10-12T10:00:00Z",
        "status": "PENDING",
        "totalAmount": order_data.totalAmount,
        "items": [{"product_id": item.product_id, "quantity": item.quantity} for item in order_data.items]
    }
    
    # Вызываем gRPC-метод оплаты через Circuit Breaker
    try:
        # Вызов функции, обёрнутой в предохранитель
        @db_breaker
        def call_grpc_payment():
            # Устанавливаем gRPC-канал с контейнером payment-service на порту 50051
            with grpc.insecure_channel('payment-service:50051') as channel:
                stub = payment_pb2_grpc.PaymentServiceStub(channel)
                
                # Формируем gRPC Request по контракту .proto файла
                request = payment_pb2.PaymentRequest(
                    order_id=order_id,
                    amount=order_data.totalAmount,
                    simulate_failure=order_data.simulate_failure
                )
                
                # Делаем синхронный вызов с таймаутом в 3 секунды по ТЗ
                response = stub.CheckPaymentStatus(request, timeout=3.0)
                return response

        # Запускаем выполнение gRPC вызова
        grpc_response = call_grpc_payment()
        
        # Если вызов прошёл успешно, обновляем статус в нашей in-memory БД
        new_order["status"] = grpc_response.status
        new_order["message"] = grpc_response.message
        
    except pybreaker.CircuitBreakerError:
        # Сценарий OPEN: Цепь разомкнута. Запрос даже не пошёл в сеть, сразу включаем Fallback!
        fallback_res = payment_fallback(order_id, "Circuit Breaker находится в состоянии OPEN")
        new_order["status"] = fallback_res["status"]
        new_order["message"] = fallback_res["message"]
        
    except grpc.RpcError as e:
        # Сценарий CLOSED/HALF-OPEN: Сетевая ошибка gRPC (например, статус UNAVAILABLE)
        print(f"[gRPC Client Error] Зафиксирован сетевой сбой: {e.details()}", flush=True)
        
        # Включаем локальный Fallback, чтобы клиент не упал со статус-кодом 500
        fallback_res = payment_fallback(order_id, f"gRPC сбой: {e.details()}")
        new_order["status"] = fallback_res["status"]
        new_order["message"] = fallback_res["message"]

    # Сохраняем итоговый статус заказа в базу данных
    if order_data.userId not in orders_db:
        orders_db[order_data.userId] = []
    orders_db[order_data.userId].append(new_order)
    
    return new_order
