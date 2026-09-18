"""FastAPI entrypoint + health-check (ТЗ раздел 4).

На подэтапе 1.1 приложение поднимается и отдаёт базовый /health. Полный
мониторинг источников (таблица source_health, статус по каждому источнику)
добавляется на подэтапе 1.8.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI

from app import __version__
from app.config import get_settings

logging.basicConfig(level=get_settings().log_level)

app = FastAPI(title="switch-trading-mvp", version=__version__)


@app.get("/health")
async def health() -> dict[str, str]:
    """Базовый health-check (расширяется на подэтапе 1.8)."""
    return {"status": "ok", "version": __version__}
