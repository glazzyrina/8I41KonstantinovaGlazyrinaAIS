import json
import time
import random
import pika
from fastapi import FastAPI

app = FastAPI(title="Payment Service")

def process_order_event(ch, method, properties, body):
    """Коллбэк-функция: срабатывает автоматически при появлении нового заказа в очереди."""
    try:
        # Читаем JSON-данные из сообщения от Order Service
        order = json.loads(body)
        order_id = order.get("id")
        total_amount = order.get("totalAmount", 0.0)
        
        print(f"[Payment Service] Получено событие OrderCreated для заказа: {order_id}", flush=True)
        
        # Имитируем небольшую временную задержку процессинга банка
        time.sleep(1.0)
        
        # Детерминированная проверка ошибки по методичке для демонстрации компенсации
        if total_amount < 0:
            success = False
            reason = "Отрицательная сумма заказа недопустима"
        else:
            # Имитируем случайную проверку оплаты (70% — успех, 30% — ошибка)
            success = random.random() < 0.7
            reason = "Недостаточно средств на карте покупателя" if not success else ""

        # Настраиваем подключение к RabbitMQ для отправки ответа обратно в Order Service
        connection = pika.BlockingConnection(pika.ConnectionParameters(host='rabbitmq'))
        channel = connection.channel()
        
        if success:
            # Сценарий успеха: публикуем событие в очередь payment_completed_queue
            reply_queue = 'payment_completed_queue'
            payload = json.dumps({
                "orderId": order_id, 
                "transactionId": f"tx-{int(time.time())}"
            })
            print(f"[Payment Service] Оплата ОДОБРЕНА для заказа {order_id}. Отправляем PaymentCompleted.", flush=True)
        else:
            # Сценарий ошибки: публикуем событие в очередь payment_failed_queue
            reply_queue = 'payment_failed_queue'
            payload = json.dumps({
                "orderId": order_id, 
                "reason": reason
            })
            print(f"[Payment Service] Оплата ОТКЛОНЕНА для заказа {order_id} ({reason}). Отправляем PaymentFailed.", flush=True)
            
        # Декларируем целевую очередь ответов и отправляем персистентное сообщение
        channel.queue_declare(queue=reply_queue, durable=True)
        channel.basic_publish(
            exchange='',
            routing_key=reply_queue,
            body=payload,
            properties=pika.BasicProperties(delivery_mode=2)
        )
        connection.close()
        
        # Подтверждаем RabbitMQ, что сообщение успешно обработано и его можно удалить из очереди
        ch.basic_ack(delivery_tag=method.delivery_tag)
        
    except Exception as e:
        print(f"[Payment Service] Критическая ошибка обработки сообщения: {e}", flush=True)

def start_rabbitmq_consumer():
    """Бесконечный цикл прослушивания входящих событий от брокера сообщений с авто-переподключением."""
    # Ждём 5 секунд при самом первом старте, чтобы дать RabbitMQ время открыть порты
    time.sleep(5.0)
    
    while True:
        try:
            connection = pika.BlockingConnection(pika.ConnectionParameters(host='rabbitmq', heartbeat=600))
            channel = connection.channel()
            
            # Гарантируем создание очереди, которую мы будем слушать
            channel.queue_declare(queue='order_created_queue', durable=True)
            
            # Распределяем входящие сообщения поштучно
            channel.basic_qos(prefetch_count=1)
            
            # Подписываем нашу функцию на очередь создания заказов
            channel.basic_consume(queue='order_created_queue', on_message_callback=process_order_event)
            
            print("[Payment Service] Успешно подключен к RabbitMQ. Ожидание событий OrderCreated...", flush=True)
            channel.start_consuming()
        except Exception as e:
            print(f"[Payment Service] Брокер недоступен, повторное подключение через 5 секунд... ({e})", flush=True)
            time.sleep(5.0)

# Фоновый запуск прослушивания брокера при старте контейнера FastAPI
@app.on_event("startup")
async def startup_event():
    import threading
    threading.Thread(target=start_rabbitmq_consumer, daemon=True).start()

