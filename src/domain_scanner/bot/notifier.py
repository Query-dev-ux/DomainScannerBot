from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import InlineKeyboardMarkup, LinkPreviewOptions

from domain_scanner.bot.keyboards import domain_keyboard
from domain_scanner.bot.render import render_report
from domain_scanner.logging import get_logger
from domain_scanner.services.scanner import ScanReport

log = get_logger(__name__)

# Which chat a domain's alert belongs in, or None for the default group.
RouteLookup = Callable[[ScanReport], Awaitable[int | None]]

# api.telegram.org resets a connection now and then, and a dropped alert is a
# domain nobody hears about: wait and try again before giving up.
SEND_BACKOFF: tuple[float, ...] = (2.0, 10.0)
# Telegram's own "slow down" can ask for a long pause; past this we let it go
# rather than tie the scan up.
MAX_RETRY_AFTER = 60


class Notifier:
    """Posts into the alert group (optionally into one forum topic).

    An alert about a single domain also goes to the group of whoever owns it
    (see /route) — the default chat keeps a copy of everything, so nothing is
    only visible to one team. Everything else — sync failures, crashes, the
    startup line — belongs to nobody in particular and goes to the default chat
    alone.
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
        for attempt in range(len(SEND_BACKOFF) + 1):
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
                return
            except TelegramRetryAfter as exc:
                # Flood control names its own pause; anything else is our backoff.
                delay = min(exc.retry_after, MAX_RETRY_AFTER)
                reason = f"retry after {exc.retry_after}s"
            except (TelegramNetworkError, TelegramServerError) as exc:
                delay = SEND_BACKOFF[min(attempt, len(SEND_BACKOFF) - 1)]
                reason = f"{type(exc).__name__}: {exc}"
            except Exception:
                # A chat we were removed from, a bad id, malformed HTML — retrying
                # changes nothing, so say so once and stop.
                log.exception("notifier.send_failed", chat_id=target)
                return
            if attempt == len(SEND_BACKOFF):
                log.error("notifier.send_gave_up", chat_id=target, reason=reason)
                return
            log.warning("notifier.send_retry", chat_id=target, reason=reason, wait=delay)
            await asyncio.sleep(delay)

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
        text = render_report(report, alert=True)
        owners_chat = await self._route_for(report)
        if owners_chat is not None and owners_chat != self.chat_id:
            await self._send(text, markup, owners_chat)
        await self._send(text, markup)

    async def notify_text(self, text: str) -> None:
        await self._send(text)
