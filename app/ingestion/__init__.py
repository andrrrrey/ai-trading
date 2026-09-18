"""Слой ingestion: клиенты источников, маршрутизатор и контроль качества.

ТЗ разделы 6.1–6.6: получение данных, переключение источников, нормализация,
дедупликация, валидация и политика пропусков.
"""

from app.ingestion.normalization import (
    PriceBar,
    PriceHistory,
    QualityReport,
    normalize_fundamentals,
    normalize_price_history,
)
from app.ingestion.service import IngestionService, MarketData
from app.ingestion.source_router import SourceRouter, build_source_router

__all__ = [
    "IngestionService",
    "MarketData",
    "PriceBar",
    "PriceHistory",
    "QualityReport",
    "SourceRouter",
    "build_source_router",
    "normalize_fundamentals",
    "normalize_price_history",
]
