"""aiogram-роутер: связывает сообщения/кнопки с BotService (ТЗ раздел 12)."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import main_keyboard
from app.bot.service import WELCOME, BotService, parse_ticker
from app.bot.templates import DISCLAIMER


def build_router(service: BotService) -> Router:
    router = Router()

    @router.message(CommandStart())
    async def on_start(message: Message) -> None:
        await message.answer(WELCOME)

    @router.message(Command("history"))
    async def on_history(message: Message, command: CommandObject) -> None:
        ticker = parse_ticker(command.args or "")
        if ticker is None:
            await message.answer("Укажите тикер: <code>/history NVDA</code>")
            return
        await message.answer(await service.history(ticker))

    @router.message(F.text.regexp(r"^\$?[A-Za-z]{1,5}$"))
    async def on_ticker(message: Message) -> None:
        ticker = parse_ticker(message.text or "")
        if ticker is None:
            return
        text, signal_id = await service.analyze(ticker)
        if signal_id is None:
            await message.answer(text)  # ошибка — без клавиатуры и без торгового вывода
        else:
            await message.answer(text, reply_markup=main_keyboard(signal_id, ticker))

    @router.callback_query(F.data.startswith("sec:"))
    async def on_section(callback: CallbackQuery) -> None:
        _, section, raw_id = callback.data.split(":", 2)
        text = await service.section(int(raw_id), section)
        await callback.message.answer(text)
        await callback.answer()

    @router.callback_query(F.data.startswith("hist:"))
    async def on_history_button(callback: CallbackQuery) -> None:
        ticker = callback.data.split(":", 1)[1]
        await callback.message.answer(await service.history(ticker))
        await callback.answer()

    @router.message(F.text)
    async def on_other(message: Message) -> None:
        await message.answer(
            "Пришлите тикер (например, <code>AAPL</code>) или /history NVDA.\n\n"
            + DISCLAIMER
        )

    return router
