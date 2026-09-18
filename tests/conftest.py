"""Общие фикстуры и утилиты для тестов."""
from __future__ import annotations

from app.ingestion.base_client import HealthRecord, HealthRecorder


class FakeHealthRecorder(HealthRecorder):
    """Рекордер, собирающий события мониторинга в память — для проверок в тестах."""

    def __init__(self) -> None:
        self.records: list[HealthRecord] = []

    async def record(self, record: HealthRecord) -> None:
        self.records.append(record)

    @property
    def statuses(self) -> list[str]:
        return [r.status for r in self.records]
