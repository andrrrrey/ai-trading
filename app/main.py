"""FastAPI entrypoint + health-check (ТЗ разделы 4, 13).

/health отдаёт статус приложения и по каждому источнику данных: статус, время
последнего успешного обращения, задержку, счётчик подряд идущих ошибок и текст
последней ошибки (мониторинг источников, подэтап 1.8).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.config import get_settings
from app.db.session import create_engine, create_session_factory
from app.monitoring import SourceHealthMonitor, get_monitor
from app.monitoring.source_health import SOURCE_ROLES, latest_data_mode

logging.basicConfig(level=get_settings().log_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = create_engine()
    app.state.db_engine = engine
    session_factory = create_session_factory(engine)
    app.state.session_factory = session_factory
    app.state.source_monitor = SourceHealthMonitor(session_factory)
    try:
        yield
    finally:
        await engine.dispose()


app = FastAPI(title="switch-trading-mvp", version=__version__, lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    """Статус приложения и источников данных (ТЗ раздел 13)."""
    settings = get_settings()
    expected = ["fmp", "sec_edgar"]
    if settings.alpaca_api_key_id and settings.alpaca_api_secret_key:
        expected.append("alpaca")
    if settings.finnhub_api_key:
        expected.append("finnhub")
    monitor = getattr(app.state, "source_monitor", None)
    try:
        sources = (
            await monitor.snapshot_from_db(tuple(expected)) if monitor else get_monitor().snapshot()
        )
    except Exception:  # БД не должна превращать liveness в HTTP 500
        logging.exception("не удалось прочитать состояние источников из БД")
        sources = get_monitor().snapshot()
    sources_ok = bool(sources) and all(item["status"] == "ok" for item in sources.values())
    for name, item in sources.items():
        item["role"] = SOURCE_ROLES.get(name, "additional")

    data_mode = None
    session_factory = getattr(app.state, "session_factory", None)
    if session_factory is not None:
        try:
            data_mode = await latest_data_mode(session_factory)
        except Exception:
            logging.exception("не удалось прочитать режим источников из БД")
    return {
        "status": "ok",  # liveness приложения
        "version": __version__,
        "sources_ok": sources_ok,
        "sources": sources,
        # primary — последний расчёт выполнен на основных источниках,
        # reserve — хотя бы часть данных пришла из резервного (Alpaca/Finnhub).
        "data_mode": data_mode,
    }
