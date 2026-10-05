"""Сохраняет периоды и происхождение EPS/P-E в fundamentals_snapshot.

Revision ID: 0004_fundamentals_provenance
Revises: 0003_source_health_idx
Create Date: 2026-10-05
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_fundamentals_provenance"
down_revision: str | None = "0003_source_health_idx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("fundamentals_snapshot", sa.Column("statement_date", sa.Date(), nullable=True))
    op.add_column("fundamentals_snapshot", sa.Column("fiscal_year", sa.String(16), nullable=True))
    op.add_column("fundamentals_snapshot", sa.Column("growth_date", sa.Date(), nullable=True))
    op.add_column(
        "fundamentals_snapshot",
        sa.Column("growth_fiscal_year", sa.String(16), nullable=True),
    )
    op.add_column(
        "fundamentals_snapshot",
        sa.Column("eps_growth_basis", sa.String(64), nullable=True),
    )
    op.add_column("fundamentals_snapshot", sa.Column("eps_previous", sa.Float(), nullable=True))
    op.add_column("fundamentals_snapshot", sa.Column("eps_basis", sa.String(64), nullable=True))
    op.add_column("fundamentals_snapshot", sa.Column("pe_ttm", sa.Float(), nullable=True))
    op.add_column(
        "fundamentals_snapshot",
        sa.Column("pe_ttm_as_of", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    for column in (
        "pe_ttm_as_of",
        "pe_ttm",
        "eps_basis",
        "eps_previous",
        "eps_growth_basis",
        "growth_fiscal_year",
        "growth_date",
        "fiscal_year",
        "statement_date",
    ):
        op.drop_column("fundamentals_snapshot", column)
