"""Inline-клавиатуры бота (ТЗ раздел 12).

Кнопки привязаны к конкретному signal_id, поэтому последовательные/параллельные
запросы по разным тикерам не смешивают результаты (требование чек-листа 1.9).
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def main_keyboard(signal_id: int, ticker: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Подробнее", callback_data=f"sec:details:{signal_id}"),
                InlineKeyboardButton(text="Метрики", callback_data=f"sec:metrics:{signal_id}"),
            ],
            [
                InlineKeyboardButton(text="Risk", callback_data=f"sec:risk:{signal_id}"),
                InlineKeyboardButton(text="История", callback_data=f"hist:{ticker}"),
            ],
        ]
    )
