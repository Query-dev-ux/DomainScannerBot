from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

router = Router(name="common")

HELP_TEXT = (
    "<b>DomainScannerBot</b> — мониторинг репутации доменов.\n\n"
    "<b>Команды:</b>\n"
    "/status — сводка по статусам доменов\n"
    "/list [clean|suspicious|flagged] — список доменов\n"
    "/check &lt;домен&gt; — проверить домен прямо сейчас\n"
    "/add &lt;домен&gt; — добавить домен вручную\n"
    "/scan_now — запустить плановое сканирование\n"
    "/sync_now — подтянуть домены из PWA API\n"
)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)
