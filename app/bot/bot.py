"""Запуск Telegram-бота (aiogram 3.x, long polling; ТЗ разделы 4, 12).

Бот работает через long polling — не требует публичного HTTPS-домена на MVP.
Запускается отдельным процессом параллельно служебному FastAPI (/health).
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.bot.handlers import build_router
from app.bot.service import BotService
from app.config import get_settings
from app.db.session import create_engine, create_session_factory
from app.ingestion import IngestionService, build_source_router
from app.monitoring import get_monitor
from app.pipeline import Pipeline

logger = logging.getLogger(__name__)


def build_dispatcher(service: BotService) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(service))
    return dispatcher


async def run() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан (см. .env)")

    engine = create_engine()
    session_factory = create_session_factory(engine)
    source_router = build_source_router(settings, health_recorder=get_monitor())
    ingestion = IngestionService(source_router)
    pipeline = Pipeline(ingestion, session_factory)
    service = BotService(pipeline, session_factory)

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = build_dispatcher(service)
    logger.info("Запуск Telegram-бота (long polling)")
    try:
        await dispatcher.start_polling(bot)
    finally:
        await source_router.aclose()
        await engine.dispose()


def main() -> None:
    logging.basicConfig(level=get_settings().log_level)
    asyncio.run(run())


if __name__ == "__main__":
    main()
