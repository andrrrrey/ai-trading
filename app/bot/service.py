"""Логика бота, отделённая от aiogram (ТЗ раздел 12) — легко тестируется.

Ошибки источников превращаются в понятные сообщения, но никогда — в торговый
вывод (техническая ошибка ≠ SELL). Каждый расчёт привязан к своему signal_id,
поэтому параллельные запросы по разным тикерам не смешиваются.
"""
from __future__ import annotations

import logging
import re

from app.bot import templates
from app.db.repository import get_signal, list_signals
from app.ingestion.base_client import (
    ERROR_AUTH,
    ERROR_INVALID_RESPONSE,
    ERROR_NETWORK,
    ERROR_NO_DATA,
    ERROR_NOT_FOUND,
    ERROR_RATE_LIMIT,
    ERROR_TIMEOUT,
    SourceError,
)
from app.ingestion.source_router import AllSourcesUnavailableError, TickerNotFoundError
from app.pipeline import Pipeline

logger = logging.getLogger(__name__)

_TICKER_RE = re.compile(r"^\$?[A-Za-z]{1,5}$")

WELCOME = (
    "👋 Пришлите тикер акции США (например, <code>AAPL</code> или <code>$NVDA</code>) — "
    "верну метрики, 7 факторных Score, Final Score и оценку риска.\n"
    "Команда /history NVDA — последние сигналы по тикеру.\n\n"
    + templates.DISCLAIMER
)


_SOURCE_TITLES = {
    "fmp": "FMP",
    "sec_edgar": "SEC EDGAR",
    "alpaca": "Alpaca",
    "finnhub": "Finnhub",
}


def source_error_message(ticker: str, exc: SourceError) -> str:
    """Понятное сообщение об ошибке источника — никогда не торговый вывод."""
    ticker = ticker.upper()
    if isinstance(exc, TickerNotFoundError):
        return f"❓ Тикер {ticker} не найден у источников данных. Проверьте написание."
    source = _SOURCE_TITLES.get(exc.source, exc.source or "источник")
    tail = "\nРасчёт не выполнен и не сохранён."
    if isinstance(exc, AllSourcesUnavailableError):
        source = f"{source} и резервный источник"
    if exc.kind == ERROR_TIMEOUT:
        text = f"⏱ {source}: превышено время ожидания ответа. Попробуйте позже."
    elif exc.kind == ERROR_NETWORK:
        text = f"🔌 {source}: источник недоступен (сетевая ошибка). Попробуйте позже."
    elif exc.kind == ERROR_RATE_LIMIT:
        text = f"⏳ {source}: превышен лимит запросов API. Повторите через минуту."
    elif exc.kind == ERROR_AUTH:
        text = (
            f"🔑 {source} отклонил запрос (HTTP {exc.status_code}): ключ API "
            "недействителен или тариф не включает эти данные. Сообщите администратору."
        )
    elif exc.kind == ERROR_INVALID_RESPONSE:
        text = f"⚠️ {source}: получен некорректный ответ API."
    elif exc.kind in (ERROR_NO_DATA, ERROR_NOT_FOUND):
        text = f"📭 {source}: нет данных по {ticker}."
    else:
        code = f" (HTTP {exc.status_code})" if exc.status_code else ""
        text = f"🔌 {source}: ошибка API{code}. Сервис данных временно недоступен."
    return text + tail


def parse_ticker(text: str) -> str | None:
    """Извлекает тикер из сообщения (NVDA / $NVDA); None если не похоже на тикер."""
    if not text:
        return None
    candidate = text.strip()
    if not _TICKER_RE.match(candidate):
        return None
    return candidate.lstrip("$").upper()


class BotService:
    def __init__(self, pipeline: Pipeline, session_factory):
        self._pipeline = pipeline
        self._session_factory = session_factory

    async def analyze(self, ticker: str) -> tuple[str, int | None]:
        """Полный расчёт по тикеру. Возвращает (текст, signal_id | None при ошибке)."""
        try:
            signal_id = await self._pipeline.run(ticker)
        except SourceError as exc:
            logger.warning("расчёт %s остановлен: %s (kind=%s)", ticker, exc, exc.kind)
            return (source_error_message(ticker, exc), None)
        except Exception:  # noqa: BLE001 — техошибка не должна ронять бот
            logger.exception("ошибка расчёта по тикеру %s", ticker)
            return ("⚠️ Внутренняя ошибка расчёта. Попробуйте позже.", None)

        async with self._session_factory() as session:
            signal = await get_signal(session, signal_id)
        return (templates.render_main(signal), signal_id)

    async def section(self, signal_id: int, section: str) -> str:
        """Рендер раздела по кнопке (details / metrics / risk) для конкретного сигнала."""
        async with self._session_factory() as session:
            signal = await get_signal(session, signal_id)
        if signal is None:
            return "Сигнал не найден — запросите расчёт заново."
        if section == "details":
            return templates.render_details(signal)
        if section == "metrics":
            return templates.render_metrics(signal)
        if section == "risk":
            return templates.render_risk(signal)
        return "Неизвестный раздел."

    async def history(self, ticker: str, *, limit: int = 10) -> str:
        async with self._session_factory() as session:
            signals = await list_signals(session, ticker, limit=limit)
        return templates.render_history(ticker, signals)
