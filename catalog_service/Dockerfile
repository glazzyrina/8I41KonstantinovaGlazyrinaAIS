# Использование официального легкого образа Python
FROM python:3.10-slim

# Настройка рабочей директории в контейнере
WORKDIR /app

# Копирование и установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование всего исходного кода каталога
COPY . .

# Команда для запуска приложения через uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
