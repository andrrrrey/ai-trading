"""SQLAlchemy 2.0 ORM-модели (ТЗ раздел 5).

Схема повторяет таблицы ТЗ. jsonb-поля используют вариант JSONB на PostgreSQL и
обычный JSON на прочих диалектах (для локальных тестов на SQLite). Таблица signals
— append-only: одна строка = один сигнал, с полным raw_input_snapshot для
воспроизводимости (ТЗ 7, 13).
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# jsonb на PostgreSQL, json — на остальных диалектах (SQLite в тестах).
JSONType = JSON().with_variant(JSONB(), "postgresql")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Ticker(Base):
    """Справочник запрашивавшихся тикеров."""

    __tablename__ = "tickers"

    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255))
    exchange: Mapped[str | None] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class PriceHistory(Base):
    """OHLCV для расчёта индикаторов; UNIQUE(ticker, date) — upsert без дублей."""

    __tablename__ = "price_history"
    __table_args__ = (
        UniqueConstraint("ticker", "date", name="uq_price_history_ticker_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    date: Mapped[date] = mapped_column(Date)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FundamentalsSnapshot(Base):
    """Фундаментальные метрики на момент расчёта."""

    __tablename__ = "fundamentals_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    as_of_date: Mapped[date] = mapped_column(Date)
    revenue_growth: Mapped[float | None] = mapped_column(Float)
    eps_growth: Mapped[float | None] = mapped_column(Float)
    eps: Mapped[float | None] = mapped_column(Float)
    gross_margin: Mapped[float | None] = mapped_column(Float)
    debt_equity: Mapped[float | None] = mapped_column(Float)
    pe: Mapped[float | None] = mapped_column(Float)
    forward_pe: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32))


class MarketContext(Base):
    """Служебные параметры для Risk Filter (ТЗ раздел 5)."""

    __tablename__ = "market_context"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    as_of_date: Mapped[date] = mapped_column(Date)
    next_earnings_date: Mapped[date | None] = mapped_column(Date)
    gap_pct: Mapped[float | None] = mapped_column(Float)
    avg_volume_20d: Mapped[float | None] = mapped_column(Float)
    distance_to_ema_pct: Mapped[float | None] = mapped_column(Float)
    news_sentiment: Mapped[float | None] = mapped_column(Float)
    negative_news_flag: Mapped[bool | None] = mapped_column(Boolean)


class FormulaVersionRow(Base):
    """Версионирование весов/порогов; ровно одна версия is_active."""

    __tablename__ = "formula_versions"

    version: Mapped[str] = mapped_column(String(32), primary_key=True)
    weights: Mapped[dict] = mapped_column(JSONType)
    thresholds: Mapped[dict] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Signal(Base):
    """История сигналов — append-only: одна строка = один расчёт (ТЗ 13)."""

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    price: Mapped[float | None] = mapped_column(Float)
    final_score: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    risk_flags: Mapped[list] = mapped_column(JSONType, default=list)
    formula_version: Mapped[str] = mapped_column(String(32))
    ai_explanation: Mapped[dict | None] = mapped_column(JSONType)
    # Все входные значения метрик на момент расчёта — основа воспроизводимости.
    raw_input_snapshot: Mapped[dict] = mapped_column(JSONType)

    factor_scores: Mapped[FactorScoresRow] = relationship(
        back_populates="signal", cascade="all, delete-orphan", uselist=False
    )


class FactorScoresRow(Base):
    """7 факторных Score, привязанных к сигналу."""

    __tablename__ = "factor_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(
        ForeignKey("signals.id", ondelete="CASCADE"), unique=True, index=True
    )
    momentum: Mapped[int | None] = mapped_column(Integer)
    growth: Mapped[int | None] = mapped_column(Integer)
    fundamentals: Mapped[int | None] = mapped_column(Integer)
    relative_strength: Mapped[int | None] = mapped_column(Integer)
    volume: Mapped[int | None] = mapped_column(Integer)
    valuation: Mapped[int | None] = mapped_column(Integer)
    catalysts: Mapped[int | None] = mapped_column(Integer)

    signal: Mapped[Signal] = relationship(back_populates="factor_scores")


class SourceHealth(Base):
    """Мониторинг источников (заполняется на подэтапе 1.8)."""

    __tablename__ = "source_health"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    status: Mapped[str] = mapped_column(String(16))
    latency_ms: Mapped[float | None] = mapped_column(Float)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)


class TelegramUser(Base):
    """Базовый учёт пользователей бота (заполняется на подэтапе 1.9)."""

    __tablename__ = "telegram_users"

    chat_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(255))
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
