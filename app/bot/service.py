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
from app.ingestion.base_client import SourceError
from app.ingestion.source_router import AllSourcesUnavailableError
from app.pipeline import Pipeline

logger = logging.getLogger(__name__)

_TICKER_RE = re.compile(r"^\$?[A-Za-z]{1,5}$")

WELCOME = (
    "👋 Пришлите тикер акции США (например, <code>AAPL</code> или <code>$NVDA</code>) — "
    "верну метрики, 7 факторных Score, Final Score и оценку риска.\n"
    "Команда /history NVDA — последние сигналы по тикеру.\n\n"
    + templates.DISCLAIMER
)


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
        except AllSourcesUnavailableError:
            return ("🔌 Сервис данных временно недоступен, попробуйте позже.", None)
        except SourceError:
            return (
                f"❓ Не удалось найти тикер {ticker.upper()}. Проверьте написание.",
                None,
            )
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
