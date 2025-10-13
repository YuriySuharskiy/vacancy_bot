# 1. Базовий образ
FROM python:3.11-slim

# 2. Робоча директорія в контейнері
WORKDIR /app

# 3. Копіюємо файли проекту
COPY . /app

# 4. Встановлюємо залежності
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# 5. Додаємо змінні середовища з .env (опціонально)
# ENV OPENAI_API_KEY=<твій ключ>
# ENV TELEGRAM_TOKEN=<твій токен>

# 6. Вказуємо команду запуску
CMD ["python", "main.py"]
