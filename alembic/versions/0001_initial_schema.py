"""Начальная схема БД (все таблицы раздела 5 ТЗ).

Базовая (initial) миграция создаёт полную схему MVP. Последующие изменения схемы
оформляются отдельными миграциями (одна на изменение).

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-19
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.models import JSONType

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tickers",
        sa.Column("ticker", sa.String(16), primary_key=True),
        sa.Column("name", sa.String(255)),
        sa.Column("exchange", sa.String(32)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "price_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(16), nullable=False, index=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ticker", "date", name="uq_price_history_ticker_date"),
    )

    op.create_table(
        "fundamentals_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(16), nullable=False, index=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("revenue_growth", sa.Float()),
        sa.Column("eps_growth", sa.Float()),
        sa.Column("eps", sa.Float()),
        sa.Column("gross_margin", sa.Float()),
        sa.Column("debt_equity", sa.Float()),
        sa.Column("pe", sa.Float()),
        sa.Column("forward_pe", sa.Float()),
        sa.Column("source", sa.String(32), nullable=False),
    )

    op.create_table(
        "market_context",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(16), nullable=False, index=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("next_earnings_date", sa.Date()),
        sa.Column("gap_pct", sa.Float()),
        sa.Column("avg_volume_20d", sa.Float()),
        sa.Column("distance_to_ema_pct", sa.Float()),
        sa.Column("news_sentiment", sa.Float()),
        sa.Column("negative_news_flag", sa.Boolean()),
    )

    op.create_table(
        "formula_versions",
        sa.Column("version", sa.String(32), primary_key=True),
        sa.Column("weights", JSONType, nullable=False),
        sa.Column("thresholds", JSONType, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.create_table(
        "signals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(16), nullable=False, index=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("price", sa.Float()),
        sa.Column("final_score", sa.Integer()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("risk_flags", JSONType, nullable=False),
        sa.Column("formula_version", sa.String(32), nullable=False),
        sa.Column("ai_explanation", JSONType),
        sa.Column("raw_input_snapshot", JSONType, nullable=False),
    )

    op.create_table(
        "factor_scores",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "signal_id",
            sa.Integer(),
            sa.ForeignKey("signals.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column("momentum", sa.Integer()),
        sa.Column("growth", sa.Integer()),
        sa.Column("fundamentals", sa.Integer()),
        sa.Column("relative_strength", sa.Integer()),
        sa.Column("volume", sa.Integer()),
        sa.Column("valuation", sa.Integer()),
        sa.Column("catalysts", sa.Integer()),
    )

    op.create_table(
        "source_health",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source", sa.String(32), nullable=False, index=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("latency_ms", sa.Float()),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text()),
    )

    op.create_table(
        "telegram_users",
        sa.Column("chat_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("username", sa.String(255)),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    for table in (
        "telegram_users",
        "source_health",
        "factor_scores",
        "signals",
        "formula_versions",
        "market_context",
        "fundamentals_snapshot",
        "price_history",
        "tickers",
    ):
        op.drop_table(table)
