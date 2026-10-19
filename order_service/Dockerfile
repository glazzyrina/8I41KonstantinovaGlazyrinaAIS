FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Принудительная установка pika прямо в слое сборки, минуя кэш requirements.txt
RUN pip install --no-cache-dir pika==1.3.2

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
