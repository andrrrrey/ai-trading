"""Оркестратор расчёта сигнала (ТЗ раздел 2).

Пайплайн: ingestion → Feature Engine → Score Engine (7 факторов → Final Score)
→ Risk Filter → сохранение сигнала в историю. Rule Engine (торговый статус) и
AI-объяснение подключаются на Этапе 2 (2.2, 2.3); в Этапе 1 в signals.status
пишется провизорный потолок статуса из Risk Filter.

Пайплайн не подменяет отсутствующие данные: недоступность источников
пробрасывается как ошибка (её обрабатывает бот, ТЗ раздел 12), неполнота данных
помечается в Risk Filter (missing_data) и в самом сигнале.
"""

from __future__ import annotations

import logging
from datetime import datetime

from app.calculation import build_raw_inputs, calculate
from app.db.repository import save_signal, upsert_active_formula_version
from app.ingestion.base_client import SourceError
from app.ingestion.normalization import NEW_YORK
from app.ingestion.source_router import source_mode
from app.scoring import (
    ScoringConfig,
    active_formula_version,
    load_scoring_config,
)

logger = logging.getLogger(__name__)

BENCHMARK_TICKER = "SPY"


def _source_entry(data_kind: str, source: str | None, fetched_at: datetime | None) -> dict:
    return {
        "source": source,
        "mode": source_mode(data_kind, source),
        "fetched_at": fetched_at.isoformat() if fetched_at else None,
    }


class Pipeline:
    def __init__(self, ingestion, session_factory, config: ScoringConfig | None = None):
        self._ingestion = ingestion
        self._session_factory = session_factory
        self._config = config or load_scoring_config()

    async def run(self, ticker: str) -> int:
        """Прогоняет тикер по всей цепочке и сохраняет сигнал; возвращает signal_id."""
        ticker = ticker.upper()
        market = await self._ingestion.get_market_data(ticker)

        data_issues = list(market.data_issues)
        benchmark = None
        try:
            benchmark, _ = await self._ingestion.get_price_history(BENCHMARK_TICKER)
        except SourceError as exc:
            logger.warning("бенчмарк %s недоступен — Relative Strength = н/д", BENCHMARK_TICKER)
            data_issues.append(f"бенчмарк {BENCHMARK_TICKER} недоступен ({exc.source}: {exc.kind})")

        # Дата расчёта фиксируется в торговой зоне и сохраняется: от неё зависит
        # event_risk, и повтор расчёта по истории использует ту же дату.
        calc_date = datetime.now(NEW_YORK).date()
        earnings_q = market.quality.get("earnings")
        earnings_available = not (earnings_q and earnings_q.is_incomplete)
        inputs = dict(
            price_history=market.price_history,
            benchmark=benchmark,
            fundamentals=market.fundamentals,
            next_earnings_date=market.earnings.next_earnings_date,
            earnings_available=earnings_available,
            news=market.news,
            filings=market.filings,
            calc_date=calc_date,
            news_available=market.news_available,
            filings_available=market.filings_available,
            data_issues=data_issues,
        )
        result = calculate(**inputs, config=self._config)
        raw_inputs = build_raw_inputs(
            **inputs,
            quote=market.quote,
            profile=market.profile,
            warnings=market.warnings,
            quality={name: q.model_dump(mode="json") for name, q in market.quality.items()},
        )
        features, final, risk = result.features, result.final, result.risk
        # Провизорный статус в Этапе 1 — потолок из Risk Filter; реальный статус
        # даст Rule Engine на подэтапе 2.2. Пользователю он не показывается.
        status = risk.allowed_max_status

        price_source = market.price_history.source
        news_sources = sorted({item.source for item in market.news})
        source_context = {
            "price_history": _source_entry(
                "price_history", price_source, market.price_history.fetched_at
            ),
            "fundamentals": _source_entry(
                "fundamentals", market.fundamentals.source, market.fundamentals.fetched_at
            ),
            "earnings": _source_entry(
                "earnings", market.earnings.source, market.earnings.fetched_at
            ),
            "benchmark": _source_entry(
                "price_history",
                benchmark.source if benchmark else None,
                benchmark.fetched_at if benchmark else None,
            ),
            "news": news_sources,
            "news_mode": (
                source_mode("news", news_sources[0])
                if news_sources
                else ("no_data" if market.news_available else "unavailable")
            ),
            "filings": sorted({item.source for item in market.filings}),
            "quote": _source_entry(
                "quote",
                market.quote.source if market.quote else None,
                market.quote.fetched_at if market.quote else None,
            ),
            "profile": _source_entry(
                "profile",
                market.profile.source if market.profile else None,
                market.profile.fetched_at if market.profile else None,
            ),
        }
        modes = [
            entry["mode"] for entry in source_context.values() if isinstance(entry, dict)
        ] + [source_context["news_mode"]]
        source_context["mode"] = "reserve" if "reserve" in modes else "primary"

        async with self._session_factory() as session:
            await upsert_active_formula_version(session, active_formula_version(self._config))
            signal = await save_signal(
                session,
                features=features,
                fundamentals=market.fundamentals,
                final=final,
                risk=risk,
                status=status,
                # Цена сигнала — текущая котировка; без неё — закрытие последней
                # дневной свечи (это явно показывается пользователю).
                price=market.quote.price if market.quote else features.price,
                news_sentiment=result.catalysts.sentiment,
                catalyst_context=result.catalysts.model_dump(mode="json"),
                formula_snapshot=active_formula_version(self._config).snapshot(),
                source_context=source_context,
                raw_inputs=raw_inputs,
                data_quality=(
                    result.data_quality.model_dump() if result.data_quality else None
                ),
            )
            logger.info(
                "signal saved id=%s ticker=%s final=%s mode=%s",
                signal.id,
                ticker,
                final.final_score,
                source_context["mode"],
            )
            return signal.id
