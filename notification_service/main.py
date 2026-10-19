import json
import pika
from fastapi import FastAPI

app = FastAPI(title="Notification Service")

def process_notification_event(ch, method, properties, body):
    """Коллбэк: вызывается при успешной оплате или при отмене заказа."""
    try:
        data = json.loads(body)
        order_id = data.get("orderId")
        routing_key = method.routing_key
        
        # Логируем отправку уведомлений строго по требованиям методички
        if routing_key == 'payment_completed_queue':
            print(f"[Notification Service] УВЕДОМЛЕНИЕ: Заказ {order_id} успешно оплачен! Отправлено уведомление клиенту.", flush=True)
        elif routing_key == 'order_cancelled_queue':
            reason = data.get("reason", "Отклонено банком")
            print(f"[Notification Service] УВЕДОМЛЕНИЕ: Заказ {order_id} отменен ({reason})! Отправлено уведомление клиенту.", flush=True)
            
        # Подтверждаем обработку сообщения брокеру
        ch.basic_ack(delivery_tag=method.delivery_tag)
    except Exception as e:
        print(f"[Notification Service] Ошибка обработки уведомления: {e}", flush=True)

def start_notification_consumer():
    """Слушаем очереди успешных оплат и отменённых заказов."""
    try:
        connection = pika.BlockingConnection(pika.ConnectionParameters(host='rabbitmq'))
        channel = connection.channel()
        
        # Гарантируем существование нужных очередей
        channel.queue_declare(queue='payment_completed_queue', durable=True)
        channel.queue_declare(queue='order_cancelled_queue', durable=True)
        
        # Подписываем обработчик на обе очереди
        channel.basic_consume(queue='payment_completed_queue', on_message_callback=process_notification_event)
        channel.basic_consume(queue='order_cancelled_queue', on_message_callback=process_notification_event)
        
        print("[Notification Service] Успешно подключен к RabbitMQ. Ожидание событий для отправки уведомлений...", flush=True)
        channel.start_consuming()
    except Exception as e:
        print(f"[Notification Service] Ошибка запуска consumer: {e}", flush=True)

@app.on_event("startup")
async def startup_event():
    import threading
    # Выносим бесконечный цикл прослушивания брокера в отдельный независимый поток
    threading.Thread(target=start_notification_consumer, daemon=True).start()