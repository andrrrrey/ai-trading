"""FastAPI entrypoint + health-check (ТЗ разделы 4, 13).

/health отдаёт статус приложения и по каждому источнику данных: статус, время
последнего успешного обращения, задержку, счётчик подряд идущих ошибок и текст
последней ошибки (мониторинг источников, подэтап 1.8).
"""
from __future__ import annotations

import logging

from fastapi import FastAPI

from app import __version__
from app.config import get_settings
from app.monitoring import get_monitor

logging.basicConfig(level=get_settings().log_level)

app = FastAPI(title="switch-trading-mvp", version=__version__)


@app.get("/health")
async def health() -> dict:
    """Статус приложения и источников данных (ТЗ раздел 13)."""
    monitor = get_monitor()
    sources = monitor.snapshot()
    return {
        "status": "ok",  # liveness приложения
        "version": __version__,
        "sources_ok": monitor.all_ok(),
        "sources": sources,
    }
