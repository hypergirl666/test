FROM python:3.10.18-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Устанавливаем Poetry
RUN pip install --no-cache-dir poetry

# Копируем только конфиги
COPY pyproject.toml poetry.lock* /app/

# Устанавливаем зависимости
RUN poetry config virtualenvs.create false \
    && poetry install --no-root --no-interaction --no-ansi

# Копируем весь проект
COPY . /app

# Запуск бота
CMD ["python", "-m", "bot.main"]
