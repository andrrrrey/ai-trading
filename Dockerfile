FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Сначала метаданные проекта — для кэширования слоя зависимостей.
COPY pyproject.toml README.md requirements.lock ./
COPY app ./app
# requirements.lock фиксирует точные версии зависимостей (перечень и лицензии —
# docs/THIRD_PARTY_LICENSES.md), чтобы сборка была воспроизводимой.
RUN pip install --upgrade pip && pip install -c requirements.lock .

COPY config ./config
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY scripts ./scripts

# По умолчанию поднимается служебный FastAPI (health/оркестратор).
# Telegram-бот запускается отдельным процессом/сервисом (подэтап 1.9).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
