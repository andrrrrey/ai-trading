"""Фоновая проверка источников и классификация состояния для /health (чек-лист 1.8)."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest_asyncio
import respx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import SourceHealth
from app.db.session import create_all
from app.ingestion.alpaca_client import AlpacaClient
from app.ingestion.fmp_client import FMPClient
from app.ingestion.source_router import SourceRouter
from app.monitoring.probe import probe_once
from app.monitoring.source_health import (
    SourceHealthMonitor,
    SourceState,
    classify,
    overall_mode,
)


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    await create_all(engine)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _router(monitor) -> SourceRouter:
    return SourceRouter(
        fmp=FMPClient(api_key="K", max_retries=1, health_recorder=monitor),
        alpaca=AlpacaClient(
            api_key_id="I", api_secret_key="S", max_retries=1, health_recorder=monitor
        ),
    )


@respx.mock
async def test_probe_checks_reserve_sources_without_user_requests(session_factory):
    respx.get(re.compile(r".*/stable/quote")).mock(
        return_value=httpx.Response(200, json=[{"price": 500.0, "timestamp": 1758139200}])
    )
    respx.get(re.compile(r".*/v2/stocks/SPY/trades/latest")).mock(
        return_value=httpx.Response(200, json={"trade": {"p": 500.1, "t": "2026-09-30T19:59:59Z"}})
    )
    monitor = SourceHealthMonitor(session_factory)

    results = await probe_once(_router(monitor), monitor)

    assert results == {"fmp": "ok", "alpaca": "ok"}
    snapshot = await monitor.snapshot_from_db(("fmp", "alpaca", "sec_edgar"))
    assert snapshot["alpaca"]["state"] == "ok"  # резерв больше не «unknown»
    assert snapshot["fmp"]["last_latency_ms"] is not None
    assert snapshot["sec_edgar"]["state"] == "unknown"  # не подключён в этом тесте
    assert overall_mode(snapshot) == "primary"


@respx.mock
async def test_probe_records_failure_classes(session_factory):
    respx.get(re.compile(r".*/stable/quote")).mock(side_effect=httpx.ReadTimeout("slow"))
    respx.get(re.compile(r".*/v2/stocks/SPY/trades/latest")).mock(
        return_value=httpx.Response(200, json={"trade": None})
    )
    monitor = SourceHealthMonitor(session_factory)

    results = await probe_once(_router(monitor), monitor)

    assert results == {"fmp": "timeout", "alpaca": "no_data"}
    snapshot = await monitor.snapshot_from_db(("fmp", "alpaca"))
    assert snapshot["fmp"]["last_error_kind"] == "timeout"
    assert snapshot["alpaca"]["last_error_kind"] == "no_data"
    assert snapshot["fmp"]["errors_24h"] == 1


def test_classify_states_and_mode():
    now = datetime.now(UTC)
    ok = classify(SourceState(source="fmp", status="ok", last_checked_at=now,
                              last_success_at=now, last_latency_ms=100), now)
    slow = classify(SourceState(source="fmp", status="ok", last_checked_at=now,
                                last_success_at=now, last_latency_ms=9000), now)
    flaky = classify(SourceState(source="fmp", status="error", last_checked_at=now,
                                 last_success_at=now - timedelta(minutes=5),
                                 consecutive_errors=1, last_error="timeout: x"), now)
    down = classify(SourceState(source="fmp", status="error", last_checked_at=now,
                                last_success_at=now - timedelta(hours=2),
                                consecutive_errors=1, last_error="network: x"), now)
    assert [ok.state, slow.state, flaky.state, down.state] == [
        "ok", "degraded", "degraded", "unavailable"
    ]
    assert flaky.last_error_kind == "timeout"
    assert overall_mode({"fmp": {"state": "unavailable"}, "alpaca": {"state": "ok"}}) == "reserve"
    assert overall_mode({"fmp": {"state": "unavailable"}, "alpaca": {"state": "unavailable"}}) == (
        "unavailable"
    )


async def test_recovery_after_failure_needs_no_manual_fix(session_factory):
    async with session_factory() as s:
        s.add(SourceHealth(source="fmp", checked_at=datetime.now(UTC) - timedelta(minutes=2),
                           status="error", latency_ms=1.0, error_count=3,
                           last_error="network: down"))
        await s.commit()
    monitor = SourceHealthMonitor(session_factory)
    assert (await monitor.snapshot_from_db(("fmp",)))["fmp"]["state"] == "unavailable"

    from app.ingestion.base_client import HealthRecord

    await monitor.record(HealthRecord("fmp", "ok", 80.0, None))
    state = (await monitor.snapshot_from_db(("fmp",)))["fmp"]
    assert state["state"] == "ok"
    assert state["total_errors"] == 1  # история сбоя сохранена
