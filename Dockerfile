# Используем официальный Python образ
FROM python:3.12-slim

# Рабочая директория
WORKDIR /app

# Системные зависимости для аудио (ffmpeg, libsndfile)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Сначала копируем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем проект
COPY . .

# Порт бэкенда
EXPOSE 8001

# Запуск сервера через модуль (server.main)
CMD ["python", "-m", "server.main"]
