"""FastAPI entrypoint + health-check (ТЗ разделы 4, 13).

/health отдаёт статус приложения и по каждому источнику данных: статус, время
последнего успешного обращения, задержку, счётчик подряд идущих ошибок и текст
последней ошибки (мониторинг источников, подэтап 1.8). Фоновый probe
(app.monitoring.probe) раз в HEALTH_PROBE_INTERVAL_SECONDS проверяет все
подключённые источники, включая резервные.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from sqlalchemy import text

from app import __version__
from app.config import get_settings
from app.db.repository import ensure_formula_version_consistent
from app.db.session import create_engine, create_session_factory
from app.ingestion import build_source_router
from app.monitoring import SourceHealthMonitor, get_monitor
from app.monitoring.probe import run_probe_loop
from app.monitoring.source_health import SOURCE_ROLES, latest_data_mode, overall_mode

logging.basicConfig(level=get_settings().log_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.require("api")
    engine = create_engine()
    app.state.db_engine = engine
    session_factory = create_session_factory(engine)
    app.state.session_factory = session_factory
    # thresholds.yaml изменён без смены номера версии → не стартуем (ТЗ 7.2, 8).
    await ensure_formula_version_consistent(session_factory)
    monitor = SourceHealthMonitor(session_factory)
    app.state.source_monitor = monitor
    # Фоновая проверка источников: /health актуален и без запросов пользователей.
    probe_router = None
    probe_task = None
    if settings.health_probe_interval_seconds > 0:
        probe_router = build_source_router(settings, health_recorder=monitor)
        probe_task = asyncio.create_task(
            run_probe_loop(
                probe_router,
                monitor,
                settings.health_probe_interval_seconds,
                retention_days=settings.source_health_retention_days,
            )
        )
    try:
        yield
    finally:
        if probe_task is not None:
            probe_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await probe_task
        if probe_router is not None:
            await probe_router.aclose()
        await engine.dispose()


app = FastAPI(title="switch-trading-mvp", version=__version__, lifespan=lifespan)

# Предел ожидания ответа БД в /ready, сек.
READY_DB_TIMEOUT_SECONDS = 5.0


@app.get("/ready")
async def ready(response: Response) -> dict:
    """Готовность к работе: PostgreSQL отвечает на ``SELECT 1``.

    В отличие от /health (liveness: процесс жив, всегда HTTP 200) возвращает
    HTTP 503, если БД недоступна. По нему Docker Compose определяет healthy.
    """
    engine = getattr(app.state, "db_engine", None)
    try:
        if engine is None:
            raise RuntimeError("подключение к БД не инициализировано")
        async with asyncio.timeout(READY_DB_TIMEOUT_SECONDS):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — любая ошибка БД = «не готов»
        logging.warning("ready: БД недоступна: %r", exc)
        response.status_code = 503
        return {"status": "unavailable", "database": "error", "error": type(exc).__name__}
    return {"status": "ok", "database": "ok"}


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
    for name, item in sources.items():
        item["role"] = SOURCE_ROLES.get(name, "additional")
    sources_ok = bool(sources) and all(item.get("state") == "ok" for item in sources.values())

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
        # Текущий режим по состоянию источников цен: primary | reserve |
        # degraded | unavailable | unknown (обновляется фоновым probe).
        "mode": overall_mode(sources),
        "sources": sources,
        # primary — последний расчёт выполнен на основных источниках,
        # reserve — хотя бы часть данных пришла из резервного (Alpaca/Finnhub).
        "data_mode": data_mode,
    }
