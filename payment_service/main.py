import time
import random
from concurrent import futures
import grpc

# Импортируем сгенерированные компилятором gRPC файлы
import payment_pb2
import payment_pb2_grpc

class PaymentServiceServicer(payment_pb2_grpc.PaymentServiceServicer):
    """Класс-обработчик входящих gRPC-запросов на проверку оплаты."""

    def CheckPaymentStatus(self, request, context):
        print(f"[gRPC Payment Server] Получен запрос для Заказа {request.order_id} на сумму {request.amount}", flush=True)

        # Детерминированная симуляция падения по флагу для удобства защиты
        if request.simulate_failure:
            print(f"[gRPC Payment Server] Имитация критического сбоя по запросу клиента!", flush=True)
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details("Платежный шлюз временно недоступен (UNAVAILABLE)")
            return payment_pb2.PaymentResponse()

        # Рандомный сбой с вероятностью 30% по требованиям методички
        if random.random() < 0.3:
            print(f"[gRPC Payment Server] Технический сбой эквайринга (30% вероятность)", flush=True)
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details("Внутренняя ошибка банка. Попробуйте позже.")
            return payment_pb2.PaymentResponse()

        # Успешный сценарий проведения оплаты
        tx_id = f"grpc-tx-{int(time.time())}"
        print(f"[gRPC Payment Server] Оплата успешно проведена. ID транзакции: {tx_id}", flush=True)
        
        return payment_pb2.PaymentResponse(
            transaction_id=tx_id,
            status="SUCCESS",
            message="Платеж успешно одобрен и проведен."
        )

def serve():
    """Запуск gRPC сервера на порту 50051."""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    payment_pb2_grpc.add_PaymentServiceServicer_to_server(PaymentServiceServicer(), server)
    
    # Слушаем порт 50051 (стандартный для gRPC внутренних вызовов микросервисов)
    server.add_insecure_port('[::]:50051')
    print("[gRPC Payment Server] Сервер оплаты запущен на порту 50051 и ожидает gRPC вызовов...", flush=True)
    
    server.start()
    try:
        while True:
            time.sleep(86400) # Держим процесс запущенным
    except KeyboardInterrupt:
        server.stop(0)

if __name__ == '__main__':
    serve()
