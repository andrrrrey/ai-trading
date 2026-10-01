"""Запуск Telegram-бота (aiogram 3.x, long polling; ТЗ разделы 4, 12).

Бот работает через long polling — не требует публичного HTTPS-домена на MVP.
Запускается отдельным процессом параллельно служебному FastAPI (/health).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.bot.handlers import build_router
from app.bot.service import BotService
from app.config import get_settings
from app.db.session import create_engine, create_session_factory
from app.ingestion import IngestionService, build_source_router
from app.monitoring import SourceHealthMonitor
from app.pipeline import Pipeline

logger = logging.getLogger(__name__)

# Heartbeat для healthcheck контейнера: файл обновляется, только если Telegram API
# отвечает (bot.get_me). Docker помечает контейнер unhealthy, если файл устарел.
HEARTBEAT_FILE = Path(os.environ.get("BOT_HEARTBEAT_FILE", "/tmp/bot-heartbeat"))
HEARTBEAT_INTERVAL_SECONDS = 60


async def _heartbeat(bot: Bot) -> None:
    while True:
        try:
            await bot.get_me()
            HEARTBEAT_FILE.write_text(str(time.time()))
        except Exception:  # noqa: BLE001 — heartbeat не должен ронять бот
            logger.warning("heartbeat: Telegram API недоступен", exc_info=True)
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)


async def prepare_polling(bot: Bot) -> None:
    """Снимает webhook: при активном webhook Telegram отклоняет getUpdates (polling).

    Накопленные обновления не сбрасываются — сообщения, отправленные боту, пока
    он был остановлен, будут обработаны.
    """
    await bot.delete_webhook(drop_pending_updates=False)
    logger.info("webhook снят (если был) — режим long polling")


def build_dispatcher(service: BotService) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(service))
    return dispatcher


async def run() -> None:
    settings = get_settings()
    settings.require("bot")

    engine = create_engine()
    session_factory = create_session_factory(engine)
    source_router = build_source_router(
        settings, health_recorder=SourceHealthMonitor(session_factory)
    )
    ingestion = IngestionService(source_router)
    pipeline = Pipeline(ingestion, session_factory)
    service = BotService(pipeline, session_factory)

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = build_dispatcher(service)
    await prepare_polling(bot)
    logger.info("Запуск Telegram-бота (long polling)")
    heartbeat = asyncio.create_task(_heartbeat(bot))
    try:
        await dispatcher.start_polling(bot)
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
        await source_router.aclose()
        await engine.dispose()


def main() -> None:
    logging.basicConfig(level=get_settings().log_level)
    asyncio.run(run())


if __name__ == "__main__":
    main()
