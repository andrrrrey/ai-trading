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
from datetime import UTC, datetime
from functools import lru_cache

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import SourceHealth
from app.ingestion.base_client import HealthRecord

logger = logging.getLogger(__name__)


class SourceState(BaseModel):
    """Актуальное состояние одного источника (для /health)."""

    source: str
    status: str = "unknown"  # ok | error | unknown
    last_checked_at: datetime | None = None
    last_success_at: datetime | None = None
    last_latency_ms: float | None = None
    consecutive_errors: int = 0
    total_errors: int = 0
    last_error: str | None = None


class SourceHealthMonitor:
    """In-memory агрегатор состояния источников + запись истории в source_health."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession] | None = None
    ):
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
            state.last_error = record.error
        self._states[record.source] = state

        if self._session_factory is not None:
            await self._persist(record, state, now)

    async def _persist(
        self, record: HealthRecord, state: SourceState, now: datetime
    ) -> None:
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
            source: state.model_dump(mode="json")
            for source, state in sorted(self._states.items())
        }

    def all_ok(self) -> bool:
        return bool(self._states) and all(
            s.status == "ok" for s in self._states.values()
        )


@lru_cache
def get_monitor() -> SourceHealthMonitor:
    """Синглтон монитора для всего приложения (клиенты и /health используют его)."""
    return SourceHealthMonitor()
