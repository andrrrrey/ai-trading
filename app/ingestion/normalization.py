"""Нормализация и контроль качества данных (ТЗ раздел 6.6).

Слой ingestion выполняет проверку качества ДО передачи данных в Feature Engine:

- Нормализация: единый формат OHLCV, торговая дата в America/New_York, USD.
- Дедупликация по (ticker, date) — последний рубеж перед price_history (UNIQUE),
  upsert без дублей.
- Валидация: обязательные поля OHLCV проверяются строгой Pydantic-схемой; бар с
  некорректными/неполными данными не проходит дальше как валидный.
- Политика пропусков: отсутствующее значение остаётся None и помечается флагом
  is_incomplete — никогда не заменяется 0 или выдуманным значением. Критичная
  нехватка данных поднимает признак, который Risk Filter превращает в missing_data
  (подэтап 1.6).

Модуль не бросает исключения на «грязных» данных: он возвращает очищенный набор и
QualityReport, чтобы вызывающий слой мог принять честное решение (в т.ч. отказать
в расчёте), а не считать по выдуманным числам.
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, model_validator

from app.ingestion.schemas import Fundamentals, RawPriceHistory, SourcedModel

NEW_YORK = ZoneInfo("America/New_York")

# Ключевые фундаментальные метрики: их отсутствие делает снапшот неполным.
KEY_FUNDAMENTAL_FIELDS = (
    "revenue_growth",
    "eps_growth",
    "eps",
    "gross_margin",
    "debt_equity",
    "pe",
)


class PriceBar(SourcedModel):
    """Валидный, нормализованный OHLCV-бар (все поля обязательны и согласованы)."""

    ticker: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float

    @model_validator(mode="after")
    def _check_consistency(self) -> PriceBar:
        for field in ("open", "high", "low", "close"):
            if getattr(self, field) <= 0:
                raise ValueError(f"{field} должно быть > 0")
        if self.volume < 0:
            raise ValueError("volume не может быть отрицательным")
        if self.high < self.low:
            raise ValueError("high < low")
        if not (self.low <= self.open <= self.high):
            raise ValueError("open вне диапазона [low, high]")
        if not (self.low <= self.close <= self.high):
            raise ValueError("close вне диапазона [low, high]")
        return self


class PriceHistory(SourcedModel):
    """Нормализованная история: бары отсортированы по дате, без дублей."""

    ticker: str
    bars: list[PriceBar]


class QualityReport(BaseModel):
    """Отчёт о качестве нормализации набора данных."""

    model_config = ConfigDict(frozen=True)

    source: str
    total_rows: int = 0
    valid_rows: int = 0
    dropped_invalid: int = 0
    duplicate_dates: int = 0
    is_incomplete: bool = False
    issues: list[str] = []


def _to_ny_date(value: date | datetime) -> date:
    """Приводит дату/время к торговой дате в зоне America/New_York."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.date()
        return value.astimezone(NEW_YORK).date()
    return value


def normalize_price_history(
    raw: RawPriceHistory, *, min_bars: int = 0
) -> tuple[PriceHistory, QualityReport]:
    """Валидирует, дедуплицирует и сортирует историю цен.

    Аргумент ``min_bars`` задаёт минимальную глубину истории (напр. 250 для EMA200,
    ТЗ раздел 7); при нехватке набор помечается is_incomplete, но не отбрасывается.
    """
    issues: list[str] = []
    by_date: dict[date, PriceBar] = {}
    dropped = 0
    duplicates = 0

    for bar in raw.bars:
        trading_date = _to_ny_date(bar.date)
        try:
            valid = PriceBar(
                ticker=raw.ticker,
                date=trading_date,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                source=bar.source,
                fetched_at=bar.fetched_at,
            )
        except ValueError:
            dropped += 1
            continue
        if trading_date in by_date:
            duplicates += 1
        by_date[trading_date] = valid  # upsert: последний по дате побеждает

    bars = [by_date[d] for d in sorted(by_date)]
    is_incomplete = len(bars) < min_bars
    if dropped:
        issues.append(f"отброшено некорректных баров: {dropped}")
    if duplicates:
        issues.append(f"дубликатов по дате: {duplicates}")
    if is_incomplete:
        issues.append(f"недостаточно истории: {len(bars)} < {min_bars} баров")

    history = PriceHistory(
        ticker=raw.ticker,
        bars=bars,
        source=raw.source,
        fetched_at=raw.fetched_at,
    )
    report = QualityReport(
        source=raw.source,
        total_rows=len(raw.bars),
        valid_rows=len(bars),
        dropped_invalid=dropped,
        duplicate_dates=duplicates,
        is_incomplete=is_incomplete,
        issues=issues,
    )
    return history, report


def normalize_fundamentals(raw: Fundamentals) -> tuple[Fundamentals, QualityReport]:
    """Помечает снапшот фундаментальных данных как неполный при пропусках.

    Значения-пропуски остаются None — никогда не заменяются 0 (ТЗ 6.6).
    """
    missing = [f for f in KEY_FUNDAMENTAL_FIELDS if getattr(raw, f) is None]
    is_incomplete = bool(missing)
    normalized = raw.model_copy(update={"is_incomplete": is_incomplete})
    issues = [f"нет значения: {f}" for f in missing]
    report = QualityReport(
        source=raw.source,
        total_rows=len(KEY_FUNDAMENTAL_FIELDS),
        valid_rows=len(KEY_FUNDAMENTAL_FIELDS) - len(missing),
        is_incomplete=is_incomplete,
        issues=issues,
    )
    return normalized, report
