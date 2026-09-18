from __future__ import annotations

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, LinkPreviewOptions

from domain_scanner.bot.keyboards import domain_keyboard
from domain_scanner.bot.render import render_report
from domain_scanner.logging import get_logger
from domain_scanner.services.scanner import ScanReport

log = get_logger(__name__)


class Notifier:
    """Posts into the alert group (optionally into one forum topic)."""

    def __init__(self, bot: Bot, chat_id: int, thread_id: int | None = None) -> None:
        self._bot = bot
        self.chat_id = chat_id
        self._thread_id = thread_id

    async def _send(self, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
        try:
            await self._bot.send_message(
                self.chat_id,
                text,
                parse_mode=ParseMode.HTML,
                message_thread_id=self._thread_id,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
                reply_markup=markup,
            )
        except Exception:
            log.exception("notifier.send_failed", chat_id=self.chat_id)

    async def notify_scan(self, report: ScanReport) -> None:
        markup = domain_keyboard(report.domain_id) if report.domain_id else None
        await self._send(render_report(report, alert=True), markup)

    async def notify_text(self, text: str) -> None:
        await self._send(text)
