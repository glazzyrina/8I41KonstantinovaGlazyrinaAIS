FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Принудительная установка gRPC и предохранителя для ЛР5
RUN pip install --no-cache-dir grpcio>=1.65.0 grpcio-tools>=1.65.0 pybreaker==1.0.1

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
