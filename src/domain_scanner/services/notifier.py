from __future__ import annotations

import html

from aiogram import Bot
from aiogram.enums import ParseMode

from domain_scanner.labels import VERDICT_EMOJI, VERDICT_RU
from domain_scanner.logging import get_logger
from domain_scanner.services.scanner import ScanReport

log = get_logger(__name__)


def _e(text: str) -> str:
    return html.escape(text, quote=False)


class Notifier:
    def __init__(self, bot: Bot, chat_id: int, thread_id: int | None = None) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._thread_id = thread_id

    async def _send(self, text: str) -> None:
        try:
            await self._bot.send_message(
                self._chat_id,
                text,
                parse_mode=ParseMode.HTML,
                message_thread_id=self._thread_id,
                disable_web_page_preview=True,
            )
        except Exception:
            log.exception("notifier.send_failed", chat_id=self._chat_id)

    def _format_report(self, report: ScanReport) -> str:
        emoji = VERDICT_EMOJI[report.verdict]
        lines = [
            f"{emoji} <b>Домен изменил статус</b>",
            "",
            f"🌐 <code>{_e(report.domain)}</code>",
            f"Было: {VERDICT_RU[report.previous_verdict]} → "
            f"Стало: <b>{VERDICT_RU[report.verdict]}</b>",
            "",
            "<b>Проверки:</b>",
        ]
        for o in report.outcomes:
            mark = VERDICT_EMOJI[o.verdict]
            detail = o.summary or o.error or "—"
            lines.append(f"{mark} <b>{_e(o.checker)}</b>: {_e(detail)}")
        return "\n".join(lines)

    async def notify_scan(self, report: ScanReport) -> None:
        await self._send(self._format_report(report))

    async def notify_text(self, text: str) -> None:
        await self._send(text)
