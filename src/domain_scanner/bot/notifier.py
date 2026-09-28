from __future__ import annotations

from collections.abc import Awaitable, Callable

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, LinkPreviewOptions

from domain_scanner.bot.keyboards import domain_keyboard
from domain_scanner.bot.render import render_report
from domain_scanner.logging import get_logger
from domain_scanner.services.scanner import ScanReport

log = get_logger(__name__)

# Which chat a domain's alert belongs in, or None for the default group.
RouteLookup = Callable[[ScanReport], Awaitable[int | None]]


class Notifier:
    """Posts into the alert group (optionally into one forum topic).

    An alert about a single domain can be routed to the group of whoever owns it
    (see /route); everything else — sync failures, crashes, the startup line —
    goes to the default chat, because it belongs to nobody in particular.
    """

    def __init__(
        self,
        bot: Bot,
        chat_id: int,
        thread_id: int | None = None,
        route_lookup: RouteLookup | None = None,
    ) -> None:
        self._bot = bot
        self.chat_id = chat_id
        self._thread_id = thread_id
        self._route_lookup = route_lookup

    async def _send(
        self,
        text: str,
        markup: InlineKeyboardMarkup | None = None,
        chat_id: int | None = None,
    ) -> None:
        target = self.chat_id if chat_id is None else chat_id
        try:
            await self._bot.send_message(
                target,
                text,
                parse_mode=ParseMode.HTML,
                # The topic belongs to the default group, not to a routed one.
                message_thread_id=self._thread_id if target == self.chat_id else None,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
                reply_markup=markup,
            )
        except Exception:
            log.exception("notifier.send_failed", chat_id=target)

    async def _route_for(self, report: ScanReport) -> int | None:
        if self._route_lookup is None:
            return None
        try:
            return await self._route_lookup(report)
        except Exception:
            # A routing table we cannot read must not cost us the alert itself.
            log.exception("notifier.route_failed", domain=report.domain)
            return None

    async def notify_scan(self, report: ScanReport) -> None:
        markup = domain_keyboard(report.domain_id) if report.domain_id else None
        await self._send(
            render_report(report, alert=True), markup, await self._route_for(report)
        )

    async def notify_text(self, text: str) -> None:
        await self._send(text)
