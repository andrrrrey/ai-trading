"""Добавляет информационный признак выхода EPS в прибыль.

Revision ID: 0005_eps_turnaround
Revises: 0004_fundamentals_provenance
Create Date: 2026-10-05
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_eps_turnaround"
down_revision: str | None = "0004_fundamentals_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "fundamentals_snapshot",
        sa.Column("eps_turnaround", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("fundamentals_snapshot", "eps_turnaround")
