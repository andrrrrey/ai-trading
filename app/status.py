"""Торговые статусы и их шкала жёсткости (ТЗ разделы 9–10).

Общий доменный тип для Risk Filter (подэтап 1.6) и Rule Engine (2.2).
Шкала жёсткости: BUY > BUY_ON_DIP > WATCH > SELL — понижение статуса означает
переход к более осторожному значению.
"""
from __future__ import annotations

from enum import StrEnum


class TradeStatus(StrEnum):
    BUY = "BUY"
    BUY_ON_DIP = "BUY_ON_DIP"
    WATCH = "WATCH"
    SELL = "SELL"


# Чем выше число — тем «агрессивнее» статус.
STATUS_HARDNESS: dict[TradeStatus, int] = {
    TradeStatus.BUY: 3,
    TradeStatus.BUY_ON_DIP: 2,
    TradeStatus.WATCH: 1,
    TradeStatus.SELL: 0,
}


def min_status(a: TradeStatus, b: TradeStatus) -> TradeStatus:
    """Более осторожный (менее агрессивный) из двух статусов."""
    return a if STATUS_HARDNESS[a] <= STATUS_HARDNESS[b] else b
