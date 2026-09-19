"""Telegram-бот (aiogram 3.x) — основной интерфейс MVP (ТЗ раздел 12)."""

from app.bot.service import BotService, parse_ticker

__all__ = ["BotService", "parse_ticker"]
