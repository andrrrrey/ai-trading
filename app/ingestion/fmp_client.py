"""Клиент Financial Modeling Prep — основной источник (ТЗ раздел 6.1).

Авторизация через query-параметр ``apikey``. Эндпоинты семейства ``/stable/*``.
Клиент возвращает DTO с проставленными ``source="fmp"`` и ``fetched_at``.
Глубокая нормализация/валидация — подэтап 1.2.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.ingestion.base_client import (
    ERROR_AUTH,
    ERROR_INVALID_RESPONSE,
    ERROR_NO_DATA,
    ERROR_RATE_LIMIT,
    BaseSourceClient,
    HealthRecorder,
    SourceError,
)
from app.ingestion.schemas import (
    CompanyProfile,
    Earnings,
    Fundamentals,
    NewsItem,
    Quote,
    RawPriceBar,
    RawPriceHistory,
)

logger = logging.getLogger(__name__)

_NEW_YORK = ZoneInfo("America/New_York")

# Ключи, в которых FMP сообщает об ошибке в теле ответа с HTTP 200.
_ERROR_KEYS = ("Error Message", "error", "Error")


def _now() -> datetime:
    return datetime.now(UTC)


def fmp_symbol(ticker: str) -> str:
    """Символ в нотации FMP: класс акции через дефис (BRK.B → BRK-B)."""
    return ticker.upper().replace(".", "-")


def _classify_error_text(text: str) -> str:
    lowered = text.lower()
    if "limit" in lowered:
        return ERROR_RATE_LIMIT
    if any(
        word in lowered
        for word in ("api key", "apikey", "upgrade", "subscription", "restricted",
                     "exclusive", "plan", "unauthorized", "forbidden")
    ):
        return ERROR_AUTH
    return ERROR_INVALID_RESPONSE


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_number(*values: Any) -> float | None:
    """Первое числовое значение из альтернативных полей.

    В отличие от ``a or b`` не теряет корректный ноль: EPS, маржа, D/E или P/E
    равные 0 — это данные, а не их отсутствие. Пропуском считаются только None,
    пустая строка и нечисловое значение.
    """
    for value in values:
        number = _to_float(value)
        if number is not None:
            return number
    return None


def _to_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


class FMPClient(BaseSourceClient):
    source = "fmp"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://financialmodelingprep.com",
        timeout: float = 15.0,
        max_retries: int = 3,
        health_recorder: HealthRecorder | None = None,
    ):
        super().__init__(
            source="fmp",
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            default_params={"apikey": api_key},
            health_recorder=health_recorder,
        )

    def _payload_error(self, payload: Any) -> tuple[str, str] | None:
        """FMP иногда отвечает HTTP 200 с телом {"Error Message": "..."}.

        Без этой проверки такой ответ выглядел бы как «пустые данные» и, например,
        превращался в «тикер не найден» вместо честной ошибки ключа/лимита.
        """
        if not isinstance(payload, dict):
            return None
        for key in _ERROR_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return _classify_error_text(value), f"ошибка API: {value.strip()[:200]}"
        return None

    async def get_quote(self, ticker: str) -> Quote:
        data = await self._get_json(
            "/stable/quote", params={"symbol": fmp_symbol(ticker)}
        )
        row = _first_row(data, source=self.source, ticker=ticker, what="quote")
        price = _to_float(row.get("price"))
        if price is None:
            raise SourceError(
                f"fmp: в котировке {ticker} нет цены", source=self.source, kind=ERROR_NO_DATA
            )
        return Quote(
            ticker=ticker,
            price=price,
            quote_time=_from_unix(row.get("timestamp")),
            source=self.source,
            fetched_at=_now(),
        )

    async def get_price_history(self, ticker: str) -> RawPriceHistory:
        data = await self._get_json(
            "/stable/historical-price-eod/full", params={"symbol": fmp_symbol(ticker)}
        )
        rows = _as_rows(data)
        fetched = _now()
        bars = [
            RawPriceBar(
                ticker=ticker,
                date=_to_date(row.get("date")),
                open=_to_float(row.get("open")),
                high=_to_float(row.get("high")),
                low=_to_float(row.get("low")),
                close=_to_float(row.get("close")),
                volume=_to_float(row.get("volume")),
                source=self.source,
                fetched_at=fetched,
            )
            for row in rows
            if _to_date(row.get("date")) is not None
        ]
        if not bars:
            # FMP на неизвестный символ отвечает HTTP 200 и пустым списком.
            raise SourceError(
                f"fmp: нет истории цен для {ticker}", source=self.source, kind=ERROR_NO_DATA
            )
        return RawPriceHistory(ticker=ticker, bars=bars, source=self.source, fetched_at=fetched)

    async def get_profile(self, ticker: str) -> CompanyProfile:
        data = await self._get_json(
            "/stable/profile", params={"symbol": fmp_symbol(ticker)}
        )
        row = _first_row(data, source=self.source, ticker=ticker, what="profile")
        active = row.get("isActivelyTrading")
        return CompanyProfile(
            ticker=ticker,
            company_name=row.get("companyName"),
            exchange=row.get("exchange") or row.get("exchangeShortName"),
            sector=row.get("sector") or None,
            industry=row.get("industry") or None,
            country=row.get("country") or None,
            currency=row.get("currency") or None,
            market_cap=_first_number(row.get("marketCap"), row.get("mktCap")),
            is_actively_trading=active if isinstance(active, bool) else None,
            source=self.source,
            fetched_at=_now(),
        )

    async def get_ratios(self, ticker: str) -> dict[str, Any]:
        data = await self._get_json(
            "/stable/ratios", params={"symbol": fmp_symbol(ticker)}
        )
        return _first_row(data, source=self.source, ticker=ticker, what="ratios")

    async def get_key_metrics(self, ticker: str) -> dict[str, Any]:
        data = await self._get_json(
            "/stable/key-metrics", params={"symbol": fmp_symbol(ticker)}
        )
        return _first_row(data, source=self.source, ticker=ticker, what="key-metrics")

    async def get_income_growth(self, ticker: str) -> dict[str, Any]:
        data = await self._get_json(
            "/stable/income-statement-growth", params={"symbol": fmp_symbol(ticker)}
        )
        return _first_row(data, source=self.source, ticker=ticker, what="income-statement-growth")

    async def get_fundamentals(self, ticker: str, period: str = "annual") -> Fundamentals:
        """Композиция ratios + key-metrics + income-statement-growth.

        Все три запроса используют один и тот же ``period`` (ТЗ 6.6) — Revenue
        Growth и EPS Growth не смешивают квартальные и годовые данные.

        Запросы выполняются независимо: сбой одного не отменяет остальные.
        Показатели из недоступного запроса остаются None, а сам запрос попадает в
        ``unavailable_parts`` — дальше правило полноты данных решает, понижена
        достоверность или Final Score не выдаётся. Если не ответил ни один
        запрос, пробрасывается ошибка источника (fundamentals нет совсем).
        """
        parts = ("ratios", "key-metrics", "income-statement-growth")
        symbol = fmp_symbol(ticker)
        *results, ttm_result = await asyncio.gather(
            *(
                self._get_json(f"/stable/{part}", params={"symbol": symbol, "period": period})
                for part in parts
            ),
            # P/E за последние 12 месяцев (TTM): годовой P/E посчитан по цене на
            # конец финансового года и может отставать на год. Необязательный запрос:
            # его сбой не делает fundamentals неполными — Valuation тогда считается
            # по годовому P/E (это видно в правиле фактора).
            self._get_json("/stable/ratios-ttm", params={"symbol": symbol}),
            return_exceptions=True,
        )
        unavailable: list[str] = []
        payloads: list[Any] = []
        for part, result in zip(parts, results, strict=True):
            if isinstance(result, SourceError):
                unavailable.append(f"{part} ({result.source}: {result.kind})")
                payloads.append(None)
            elif isinstance(result, BaseException):
                raise result
            else:
                payloads.append(result)
        if len(unavailable) == len(parts):
            raise next(r for r in results if isinstance(r, SourceError))
        if isinstance(ttm_result, SourceError):
            logger.warning("fmp ratios-ttm недоступен для %s: %s", ticker, ttm_result.kind)
            ttm_result = None
        elif isinstance(ttm_result, BaseException):
            raise ttm_result
        fetched_at = _now()
        ttm_rows = _as_rows(ttm_result)
        ttm = ttm_rows[0] if ttm_rows else {}
        ratios, metrics, growth = payloads
        ratio_rows = _as_rows(ratios)
        r = ratio_rows[:1]
        m = _as_rows(metrics)[:1]
        growth_rows = _as_rows(growth)
        g = growth_rows[:1]
        r = r[0] if r else {}
        m = m[0] if m else {}
        g = g[0] if g else {}

        eps = _first_number(r.get("netIncomePerShare"), m.get("eps"), r.get("eps"))
        statement_date = _to_date(r.get("date"))
        growth_date = _to_date(g.get("date"))
        statement_fiscal_year = (
            str(r.get("fiscalYear")) if r.get("fiscalYear") is not None else None
        )
        growth_fiscal_year = (
            str(g.get("fiscalYear")) if g.get("fiscalYear") is not None else None
        )
        periods_mismatch = (
            statement_date is not None
            and growth_date is not None
            and statement_date != growth_date
        ) or (
            statement_fiscal_year is not None
            and growth_fiscal_year is not None
            and statement_fiscal_year != growth_fiscal_year
        )
        if periods_mismatch:
            unavailable.append("income-statement-growth (fmp: period_mismatch)")

        previous = ratio_rows[1] if len(ratio_rows) > 1 else {}
        previous_eps = _first_number(
            previous.get("netIncomePerShare"), previous.get("eps")
        )
        provider_eps_growth = None if periods_mismatch else _to_float(g.get("growthEPS"))
        eps_turnaround = (
            previous_eps is not None
            and previous_eps <= 0
            and eps is not None
            and eps > 0
        )
        if periods_mismatch:
            eps_growth = None
            eps_growth_basis = "not_applicable_period_mismatch"
        elif eps_turnaround:
            eps_growth = None
            eps_growth_basis = "turnaround_no_growth_score"
        elif previous_eps is not None and previous_eps <= 0:
            # Процентный рост от нулевой/отрицательной базы не имеет устойчивого
            # экономического смысла (особенно при переходе через ноль). Не
            # превращаем такой показатель в автоматический Growth=100.
            eps_growth = None
            eps_growth_basis = "not_applicable_nonpositive_previous_eps"
        elif previous_eps is not None:
            eps_growth = provider_eps_growth
            eps_growth_basis = "fmp_growthEPS_positive_previous_eps"
        else:
            # Некоторые тарифы/моки возвращают только один период. Значение
            # сохраняем, но явно маркируем, что база не была проверена.
            eps_growth = provider_eps_growth
            eps_growth_basis = "fmp_growthEPS_base_unverified"

        pe_ttm = _first_number(
            ttm.get("priceToEarningsRatioTTM"), ttm.get("peRatioTTM")
        )
        return Fundamentals(
            ticker=ticker,
            period=period,
            statement_date=statement_date,
            fiscal_year=statement_fiscal_year,
            growth_date=growth_date,
            growth_fiscal_year=growth_fiscal_year,
            revenue_growth=None if periods_mismatch else _to_float(g.get("growthRevenue")),
            eps_growth=eps_growth,
            eps_growth_basis=eps_growth_basis,
            # В stable API FMP EPS публикуется как netIncomePerShare в ratios.
            # Старые имена оставлены как fallback для совместимости с моками и
            # предыдущими версиями API.
            eps=eps,
            eps_previous=previous_eps,
            eps_basis="annual_basic_reported" if eps is not None else None,
            eps_turnaround=eps_turnaround,
            gross_margin=_first_number(r.get("grossProfitMargin"), m.get("grossProfitMargin")),
            debt_equity=_first_number(
                r.get("debtToEquityRatio"), r.get("debtEquityRatio"), m.get("debtToEquity")
            ),
            pe=_first_number(
                r.get("priceToEarningsRatio"), r.get("priceEarningsRatio"), m.get("peRatio")
            ),
            forward_pe=_to_float(m.get("forwardPE")),
            pe_ttm=pe_ttm,
            pe_ttm_as_of=fetched_at if pe_ttm is not None else None,
            unavailable_parts=unavailable,
            source=self.source,
            fetched_at=fetched_at,
        )

    async def get_earnings(self, ticker: str) -> Earnings:
        data = await self._get_json(
            "/stable/earnings", params={"symbol": fmp_symbol(ticker)}
        )
        rows = _as_rows(data)
        next_date = _next_earnings_date(rows)
        return Earnings(
            ticker=ticker,
            next_earnings_date=next_date,
            source=self.source,
            fetched_at=_now(),
        )

    async def get_news(self, ticker: str, limit: int = 20) -> list[NewsItem]:
        data = await self._get_json(
            "/stable/news/stock", params={"symbols": fmp_symbol(ticker), "limit": limit}
        )
        fetched = _now()
        items: list[NewsItem] = []
        for row in _as_rows(data):
            title = row.get("title")
            if not title:
                continue
            published = row.get("publishedDate") or row.get("date")
            items.append(
                NewsItem(
                    ticker=ticker,
                    title=str(title),
                    published_at=_parse_dt(published),
                    url=row.get("url"),
                    source=self.source,
                    fetched_at=fetched,
                )
            )
        return items


def _from_unix(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC) if value else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _as_rows(data: Any) -> list[dict[str, Any]]:
    """Приводит ответ FMP к списку словарей (API отдаёт list или объект)."""
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _first_row(data: Any, *, source: str, ticker: str, what: str) -> dict[str, Any]:
    rows = _as_rows(data)
    if not rows:
        raise SourceError(
            f"{source}: пустой ответ {what} для {ticker}", source=source, kind=ERROR_NO_DATA
        )
    return rows[0]


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    for parser in (datetime.fromisoformat,):
        try:
            return parser(text)
        except ValueError:
            continue
    d = _to_date(value)
    return datetime(d.year, d.month, d.day, tzinfo=UTC) if d else None


def _next_earnings_date(rows: list[dict[str, Any]]) -> date | None:
    """Ближайшая будущая дата отчётности из списка earnings.

    «Сегодня» — по торговой зоне America/New_York (как и дата расчёта): иначе
    вечером по Нью-Йорку отчётность текущего дня уже считалась бы прошедшей.
    """
    today = datetime.now(_NEW_YORK).date()
    future = sorted(
        d
        for row in rows
        if (d := _to_date(row.get("date") or row.get("epsReportedDate"))) is not None and d >= today
    )
    return future[0] if future else None
