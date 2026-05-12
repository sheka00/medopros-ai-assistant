# Используем официальный Python образ
FROM python:3.12-slim

# Рабочая директория
WORKDIR /app

# Системные зависимости для аудио (ffmpeg, libsndfile) и git
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Сначала копируем зависимости
COPY requirements.txt .

# Установка всех пакетов
RUN pip install --no-cache-dir --upgrade pip setuptools wheel
RUN pip install --no-cache-dir --default-timeout=100 -r requirements.txt

# Копируем весь проект (Лендинг + Приложение в папке /app)
COPY . /app

# Порт бэкенда (теперь он обслуживает и лендинг)
EXPOSE 8001

# Запуск сервера
CMD ["python", "backend.py"]
