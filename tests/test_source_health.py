"""Тесты мониторинга источников (ТЗ раздел 13, DoD подэтапа 1.8)."""
from __future__ import annotations

import asyncio

import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import SourceHealth
from app.db.session import create_all
from app.ingestion.base_client import HealthRecord
from app.main import app
from app.monitoring import get_monitor
from app.monitoring.source_health import SourceHealthMonitor


def ok(source: str, latency: float = 50.0) -> HealthRecord:
    return HealthRecord(source, "ok", latency, None)


def err(source: str, msg: str = "HTTP 503") -> HealthRecord:
    return HealthRecord(source, "error", 12.0, msg)


# --------------------------------------------------------------------------- #
# In-memory состояние
# --------------------------------------------------------------------------- #
async def test_ok_record_sets_success():
    mon = SourceHealthMonitor()
    await mon.record(ok("fmp"))
    state = mon.snapshot()["fmp"]
    assert state["status"] == "ok"
    assert state["last_success_at"] is not None
    assert state["consecutive_errors"] == 0
    assert mon.all_ok() is True


async def test_error_increments_and_keeps_last_success():
    mon = SourceHealthMonitor()
    await mon.record(ok("fmp"))
    await mon.record(err("fmp", "bad key"))
    await mon.record(err("fmp"))
    state = mon.snapshot()["fmp"]
    assert state["status"] == "error"
    assert state["consecutive_errors"] == 2
    assert state["total_errors"] == 2
    assert state["last_error"] == "HTTP 503"
    assert state["last_success_at"] is not None  # успех из прошлого сохранён
    assert mon.all_ok() is False


async def test_recovery_resets_consecutive_errors():
    mon = SourceHealthMonitor()
    for _ in range(3):
        await mon.record(err("fmp"))
    assert mon.snapshot()["fmp"]["consecutive_errors"] == 3
    await mon.record(ok("fmp"))
    state = mon.snapshot()["fmp"]
    assert state["status"] == "ok"
    assert state["consecutive_errors"] == 0  # автоматически, без ручной правки


async def test_tracks_sources_independently():
    mon = SourceHealthMonitor()
    await mon.record(ok("fmp"))
    await mon.record(err("alpaca"))
    snap = mon.snapshot()
    assert snap["fmp"]["status"] == "ok"
    assert snap["alpaca"]["status"] == "error"


# --------------------------------------------------------------------------- #
# Запись истории в source_health (append-only)
# --------------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await create_all(engine)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_persists_to_source_health_table(session_factory):
    mon = SourceHealthMonitor(session_factory=session_factory)
    await mon.record(err("fmp", "invalid key"))
    await mon.record(ok("fmp"))
    async with session_factory() as s:
        rows = (await s.execute(select(SourceHealth))).scalars().all()
        count = await s.scalar(select(func.count()).select_from(SourceHealth))
    assert count == 2  # append-only лента проверок
    assert {r.status for r in rows} == {"ok", "error"}


# --------------------------------------------------------------------------- #
# /health endpoint: искусственный сбой виден, после восстановления — обновляется
# --------------------------------------------------------------------------- #
def test_health_endpoint_reflects_failure_and_recovery():
    get_monitor.cache_clear()
    monitor = get_monitor()
    client = TestClient(app)

    # искусственный сбой основного источника
    asyncio.run(monitor.record(err("fmp", "invalid api key")))
    body = client.get("/health").json()
    assert body["status"] == "ok"          # само приложение живо
    assert body["sources_ok"] is False
    assert body["sources"]["fmp"]["status"] == "error"
    assert body["sources"]["fmp"]["last_error"] == "invalid api key"

    # восстановление источника
    asyncio.run(monitor.record(ok("fmp")))
    body = client.get("/health").json()
    assert body["sources"]["fmp"]["status"] == "ok"
    assert body["sources_ok"] is True

    get_monitor.cache_clear()
