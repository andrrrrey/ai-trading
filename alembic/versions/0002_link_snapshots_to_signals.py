"""Связь снимков с расчётом и BigInteger для Telegram ID.

- fundamentals_snapshot.signal_id, market_context.signal_id → signals.id: по
  любой записи истории видны отчётность и рыночный контекст этого расчёта;
- telegram_users.chat_id: Integer → BigInteger (Telegram ID больше 2^31).

batch_alter_table — чтобы миграция работала и на PostgreSQL, и на SQLite (тесты).

Downgrade невозможен, пока в telegram_users есть ID > 2^31 (их нельзя вернуть
в int4): PostgreSQL прервёт откат целиком, данные не пострадают.

Revision ID: 0002_link_snapshots
Revises: 0001_initial
Create Date: 2026-10-01
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_link_snapshots"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("fundamentals_snapshot", "market_context"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("signal_id", sa.Integer(), nullable=True))
            batch.create_index(f"ix_{table}_signal_id", ["signal_id"])
            batch.create_foreign_key(
                f"fk_{table}_signal_id", "signals", ["signal_id"], ["id"], ondelete="CASCADE"
            )
    with op.batch_alter_table("telegram_users") as batch:
        batch.alter_column(
            "chat_id", existing_type=sa.Integer(), type_=sa.BigInteger(), existing_nullable=False
        )


def downgrade() -> None:
    with op.batch_alter_table("telegram_users") as batch:
        batch.alter_column(
            "chat_id", existing_type=sa.BigInteger(), type_=sa.Integer(), existing_nullable=False
        )
    for table in ("market_context", "fundamentals_snapshot"):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"fk_{table}_signal_id", type_="foreignkey")
            batch.drop_index(f"ix_{table}_signal_id")
            batch.drop_column("signal_id")
