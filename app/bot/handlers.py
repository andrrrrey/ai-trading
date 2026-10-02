"""aiogram-роутер: связывает сообщения/кнопки с BotService (ТЗ раздел 12)."""

from __future__ import annotations

import contextlib
import logging

from aiogram import BaseMiddleware, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import main_keyboard
from app.bot.service import TICKER_PATTERN, WELCOME, BotService, parse_ticker
from app.bot.templates import DISCLAIMER, split_message
from app.config import get_settings

logger = logging.getLogger(__name__)


async def send_text(message: Message, text: str, reply_markup=None) -> None:
    """Отправляет текст, при необходимости частями (лимит Telegram 4096 символов).

    Клавиатура прикрепляется к последней части.
    """
    chunks = split_message(text)
    for index, chunk in enumerate(chunks):
        last = index == len(chunks) - 1
        await message.answer(chunk, reply_markup=reply_markup if last else None)


class AllowedUsersMiddleware(BaseMiddleware):
    """Закрывает бот для пользователей вне TELEGRAM_ALLOWED_IDS."""

    def __init__(self, allowed_ids: set[int]):
        self._allowed_ids = allowed_ids

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if self._allowed_ids and (user is None or user.id not in self._allowed_ids):
            if isinstance(event, Message):
                await event.answer("Доступ к боту ограничен.")
            elif isinstance(event, CallbackQuery):
                await event.answer("Доступ ограничен.", show_alert=True)
            return None
        return await handler(event, data)


class UserTrackingMiddleware(BaseMiddleware):
    """Обновляет telegram_users (первый/последний визит) для допущенных пользователей."""

    def __init__(self, service: BotService):
        self._service = service

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user is not None:
            await self._service.touch_user(user.id, user.username)
        return await handler(event, data)


def build_router(service: BotService) -> Router:
    router = Router()
    middleware = AllowedUsersMiddleware(set(get_settings().telegram_allowed_ids))
    router.message.middleware(middleware)
    router.callback_query.middleware(middleware)
    # после проверки доступа: учитываются только допущенные пользователи
    router.message.middleware(UserTrackingMiddleware(service))

    @router.message(CommandStart())
    async def on_start(message: Message) -> None:
        await message.answer(WELCOME)

    @router.message(Command("history"))
    async def on_history(message: Message, command: CommandObject) -> None:
        ticker = parse_ticker(command.args or "")
        if ticker is None:
            await message.answer("Укажите тикер: <code>/history NVDA</code>")
            return
        await send_text(message, await service.history(ticker))

    @router.message(F.text.regexp(TICKER_PATTERN))
    async def on_ticker(message: Message) -> None:
        ticker = parse_ticker(message.text or "")
        if ticker is None:
            return
        chat_id = message.chat.id if message.chat else None
        progress = None
        if not service.is_running(ticker, chat_id):
            # Расчёт может занять до минуты (повторы, резервный источник) —
            # пользователь видит, что запрос принят, и не отправляет его повторно.
            progress = await message.answer(f"⏳ Считаю {ticker}…")
        text, signal_id = await service.analyze(ticker, chat_id=chat_id)
        if progress is not None:
            with contextlib.suppress(Exception):
                await progress.delete()
        if signal_id is None:
            await send_text(message, text)  # ошибка — без клавиатуры и торгового вывода
        else:
            await send_text(message, text, reply_markup=main_keyboard(signal_id, ticker))

    @router.callback_query(F.data.startswith("sec:"))
    async def on_section(callback: CallbackQuery) -> None:
        _, section, raw_id = callback.data.split(":", 2)
        # Сначала отвечаем на callback: Telegram ждёт ответ не дольше ~15 с.
        await callback.answer()
        text = await service.section(int(raw_id), section)
        await send_text(callback.message, text)

    @router.callback_query(F.data.startswith("hist:"))
    async def on_history_button(callback: CallbackQuery) -> None:
        ticker = callback.data.split(":", 1)[1]
        await callback.answer()
        await send_text(callback.message, await service.history(ticker))

    @router.message(F.text)
    async def on_other(message: Message) -> None:
        await message.answer(
            "Пришлите тикер (например, <code>AAPL</code>) или /history NVDA.\n\n" + DISCLAIMER
        )

    return router
