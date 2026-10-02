"""Индексы source_health для /health и очистки старых записей.

Лента проверок источников растёт на каждый HTTP-запрос; /health выбирает
последнюю проверку и считает ошибки по источнику, фоновый probe удаляет записи
старше SOURCE_HEALTH_RETENTION_DAYS. Без индексов эти запросы читают всю таблицу.

Revision ID: 0003_source_health_idx
Revises: 0002_link_snapshots
Create Date: 2026-10-02
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_source_health_idx"
down_revision: str | None = "0002_link_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_source_health_source_checked_at", "source_health", ["source", "checked_at"]
    )
    op.create_index(
        "ix_source_health_source_status_checked_at",
        "source_health",
        ["source", "status", "checked_at"],
    )
    op.create_index("ix_source_health_checked_at", "source_health", ["checked_at"])


def downgrade() -> None:
    op.drop_index("ix_source_health_checked_at", table_name="source_health")
    op.drop_index("ix_source_health_source_status_checked_at", table_name="source_health")
    op.drop_index("ix_source_health_source_checked_at", table_name="source_health")
