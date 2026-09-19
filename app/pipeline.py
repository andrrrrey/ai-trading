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

from app.db.repository import save_signal
from app.features.indicators import compute_features
from app.ingestion.base_client import SourceError
from app.risk import compute_risk
from app.scoring import (
    ScoringConfig,
    compute_factor_scores,
    compute_final_score,
    load_scoring_config,
)

logger = logging.getLogger(__name__)

BENCHMARK_TICKER = "SPY"


class Pipeline:
    def __init__(self, ingestion, session_factory, config: ScoringConfig | None = None):
        self._ingestion = ingestion
        self._session_factory = session_factory
        self._config = config or load_scoring_config()

    async def run(self, ticker: str) -> int:
        """Прогоняет тикер по всей цепочке и сохраняет сигнал; возвращает signal_id."""
        ticker = ticker.upper()
        market = await self._ingestion.get_market_data(ticker)

        benchmark = None
        try:
            benchmark, _ = await self._ingestion.get_price_history(BENCHMARK_TICKER)
        except SourceError:
            logger.warning("бенчмарк %s недоступен — Relative Strength = н/д", BENCHMARK_TICKER)

        features = compute_features(market.price_history, benchmark)
        factor_scores = compute_factor_scores(features, market.fundamentals, self._config)
        final = compute_final_score(factor_scores, self._config)
        risk = compute_risk(
            features,
            next_earnings_date=market.earnings.next_earnings_date,
            fundamentals=market.fundamentals,
        )
        # Провизорный статус в Этапе 1 — потолок из Risk Filter; реальный статус
        # даст Rule Engine на подэтапе 2.2.
        status = risk.allowed_max_status

        async with self._session_factory() as session:
            signal = await save_signal(
                session,
                features=features,
                fundamentals=market.fundamentals,
                final=final,
                risk=risk,
                status=status,
                price=features.price,
            )
            logger.info(
                "signal saved id=%s ticker=%s final=%s status=%s",
                signal.id, ticker, final.final_score, status.value,
            )
            return signal.id
