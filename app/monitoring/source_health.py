"""Мониторинг источников данных (ТЗ раздел 13).

SourceHealthMonitor реализует протокол HealthRecorder (подэтап 1.1): каждый вызов
любого клиента источника обновляет актуальное состояние источника и, при наличии
фабрики сессий, пишет строку в таблицу source_health. Эндпоинт /health отдаёт
текущий статус по каждому источнику: статус, время последнего успешного
обращения, задержка, счётчик подряд идущих ошибок и текст последней ошибки.

Состояние обновляется автоматически при каждом обращении — после восстановления
источника статус меняется на ok без ручной правки истории (append-only source_health
хранит полную ленту проверок).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Signal, SourceHealth
from app.ingestion.base_client import HealthRecord

logger = logging.getLogger(__name__)


# Пороги классификации состояния источника (ТЗ 5.1).
UNAVAILABLE_AFTER_ERRORS = 3  # подряд ошибок → unavailable
SLOW_LATENCY_MS = 5000.0  # ответ медленнее → degraded
STALE_AFTER_SECONDS = 1800  # нет успешного ответа дольше → данные устарели


class SourceState(BaseModel):
    """Актуальное состояние одного источника (для /health)."""

    source: str
    status: str = "unknown"  # результат последнего обращения: ok | error | unknown
    # Итоговое состояние: ok | degraded | unavailable | unknown
    state: str = "unknown"
    last_checked_at: datetime | None = None
    last_success_at: datetime | None = None
    # Возраст последней проверки и последнего успешного ответа, сек.
    age_seconds: float | None = None
    success_age_seconds: float | None = None
    last_latency_ms: float | None = None
    consecutive_errors: int = 0
    total_errors: int = 0
    errors_24h: int = 0
    last_error: str | None = None
    # Класс причины последней ошибки: timeout | network | rate_limit | auth | no_data …
    last_error_kind: str | None = None


def _error_kind(error: str | None) -> str | None:
    if not error or ":" not in error:
        return None
    kind = error.split(":", 1)[0].strip()
    return kind if kind.replace("_", "").isalpha() else None


def classify(state: SourceState, now: datetime | None = None) -> SourceState:
    """Проставляет state и возраст по последним проверкам источника."""
    now = now or datetime.now(UTC)
    if state.last_checked_at is not None:
        state.age_seconds = round((now - _aware(state.last_checked_at)).total_seconds(), 1)
    if state.last_success_at is not None:
        state.success_age_seconds = round(
            (now - _aware(state.last_success_at)).total_seconds(), 1
        )
    state.last_error_kind = _error_kind(state.last_error) if state.status == "error" else None
    if state.last_checked_at is None:
        state.state = "unknown"
    elif state.status == "error":
        stale = state.success_age_seconds is None or state.success_age_seconds > STALE_AFTER_SECONDS
        state.state = (
            "unavailable"
            if state.consecutive_errors >= UNAVAILABLE_AFTER_ERRORS or stale
            else "degraded"
        )
    elif (state.last_latency_ms or 0) > SLOW_LATENCY_MS:
        state.state = "degraded"
    else:
        state.state = "ok"
    return state


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def overall_mode(states: dict[str, dict]) -> str:
    """Режим работы по текущему состоянию источников цен (ТЗ 5.1, чек-лист 1.8.7).

    primary — основной FMP работает; reserve — FMP недоступен, работает Alpaca;
    degraded — FMP отвечает с ошибками/медленно; unavailable — цены взять неоткуда.
    """
    fmp = (states.get("fmp") or {}).get("state", "unknown")
    alpaca = (states.get("alpaca") or {}).get("state", "unknown")
    if fmp == "ok":
        return "primary"
    if alpaca == "ok":
        return "reserve"
    if fmp == "degraded":
        return "degraded"
    if fmp == "unknown":
        return "unknown"
    return "unavailable"


class SourceHealthMonitor:
    """In-memory агрегатор состояния источников + запись истории в source_health."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None):
        self._states: dict[str, SourceState] = {}
        self._session_factory = session_factory

    async def record(self, record: HealthRecord) -> None:
        now = datetime.now(UTC)
        state = self._states.get(record.source) or SourceState(source=record.source)
        state.status = record.status
        state.last_checked_at = now
        state.last_latency_ms = record.latency_ms
        if record.status == "ok":
            state.last_success_at = now
            state.consecutive_errors = 0
        else:
            state.consecutive_errors += 1
            state.total_errors += 1
            state.errors_24h += 1
            state.last_error = record.error
        self._states[record.source] = state
        if self._session_factory is not None:
            await self._persist(record, state, now)

    async def _persist(self, record: HealthRecord, state: SourceState, now: datetime) -> None:
        """Пишет событие в source_health (best-effort — сбой БД не ломает запрос)."""
        try:
            async with self._session_factory() as session:
                session.add(
                    SourceHealth(
                        source=record.source,
                        checked_at=now,
                        status=record.status,
                        latency_ms=record.latency_ms,
                        error_count=state.consecutive_errors,
                        last_error=record.error,
                    )
                )
                await session.commit()
        except Exception:  # noqa: BLE001 — мониторинг не должен ронять ingestion
            logger.exception("не удалось записать source_health для %s", record.source)

    def snapshot(self) -> dict[str, dict]:
        """Текущее состояние всех источников (JSON-сериализуемое) для /health."""
        return {
            source: classify(state.model_copy()).model_dump(mode="json")
            for source, state in sorted(self._states.items())
        }

    def all_ok(self) -> bool:
        return bool(self._states) and all(s.status == "ok" for s in self._states.values())

    async def snapshot_from_db(self, expected_sources: tuple[str, ...] = ()) -> dict[str, dict]:
        """Общее состояние между процессами API, бота и фонового probe через PostgreSQL.

        Агрегация выполняется запросами по каждому источнику (последняя проверка,
        последний успех, счётчики ошибок), без чтения всей ленты source_health.
        """
        if self._session_factory is None:
            return self.snapshot()
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            known = set(
                (await session.execute(select(SourceHealth.source).distinct())).scalars()
            )
            states: dict[str, SourceState] = {}
            for name in sorted(set(expected_sources) | known):
                state = SourceState(source=name)
                latest = (
                    await session.execute(
                        select(SourceHealth)
                        .where(SourceHealth.source == name)
                        .order_by(SourceHealth.checked_at.desc(), SourceHealth.id.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if latest is not None:
                    state.status = latest.status
                    state.last_checked_at = latest.checked_at
                    state.last_latency_ms = latest.latency_ms
                    state.consecutive_errors = latest.error_count
                    state.last_error = latest.last_error
                    state.last_success_at = await session.scalar(
                        select(func.max(SourceHealth.checked_at)).where(
                            SourceHealth.source == name, SourceHealth.status == "ok"
                        )
                    )
                    errors = SourceHealth.source == name, SourceHealth.status == "error"
                    state.total_errors = await session.scalar(
                        select(func.count()).select_from(SourceHealth).where(*errors)
                    )
                    state.errors_24h = await session.scalar(
                        select(func.count())
                        .select_from(SourceHealth)
                        .where(*errors, SourceHealth.checked_at >= now - timedelta(hours=24))
                    )
                states[name] = classify(state, now)
        return {name: state.model_dump(mode="json") for name, state in states.items()}


# Роль источника в системе (ТЗ 4): основной / официальный / резервный.
SOURCE_ROLES: dict[str, str] = {
    "fmp": "primary",
    "sec_edgar": "official",
    "alpaca": "reserve",
    "finnhub": "reserve",
}


async def latest_data_mode(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict | None:
    """Какие источники (основной или резервный) использованы в последнем расчёте.

    Берётся из последней записи signals — общей для процессов бота и API.
    """
    async with session_factory() as session:
        signal = (
            await session.execute(select(Signal).order_by(Signal.id.desc()).limit(1))
        ).scalar_one_or_none()
    if signal is None:
        return None
    sources = (signal.raw_input_snapshot or {}).get("sources", {})
    return {
        "mode": sources.get("mode", "unknown"),
        "signal_id": signal.id,
        "ticker": signal.ticker,
        "timestamp": signal.timestamp.isoformat(),
        "price_history": sources.get("price_history"),
        "benchmark": sources.get("benchmark"),
        "fundamentals": sources.get("fundamentals"),
        "earnings": sources.get("earnings"),
        "news": {"sources": sources.get("news"), "mode": sources.get("news_mode")},
        "filings": sources.get("filings"),
    }


@lru_cache
def get_monitor() -> SourceHealthMonitor:
    """Синглтон монитора для всего приложения (клиенты и /health используют его)."""
    return SourceHealthMonitor()
