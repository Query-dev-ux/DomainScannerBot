from __future__ import annotations

import asyncio
import contextlib

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, LinkPreviewOptions
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from domain_scanner.bot.dispatcher import build_dispatcher
from domain_scanner.bot.notifier import Notifier
from domain_scanner.bot.render import BOT_COMMANDS, render_startup
from domain_scanner.checkers import build_checkers
from domain_scanner.config import Settings, get_settings
from domain_scanner.db import init_engine, shutdown_engine
from domain_scanner.logging import configure_logging, get_logger
from domain_scanner.scheduler import register_jobs
from domain_scanner.services import DomainSyncService, ScannerService
from domain_scanner.sources import build_providers

log = get_logger(__name__)


class Application:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bot = Bot(
            token=settings.bot_token,
            default=DefaultBotProperties(
                parse_mode=ParseMode.HTML,
                link_preview=LinkPreviewOptions(is_disabled=True),
            ),
        )
        self.notifier = Notifier(self.bot, settings.alert_chat_id, settings.alert_thread_id)
        self.scanner = ScannerService(
            build_checkers(settings), concurrency=settings.scan_concurrency
        )
        self.sync_service = DomainSyncService(build_providers(settings))
        self.scheduler = AsyncIOScheduler(timezone="UTC")
        self.dp = build_dispatcher(self)

    async def _set_commands(self) -> None:
        # Populates the "Menu" button in Telegram clients.
        with contextlib.suppress(Exception):
            await self.bot.set_my_commands(
                [BotCommand(command=c, description=d) for c, d in BOT_COMMANDS]
            )

    async def run(self) -> None:
        init_engine(self.settings.database_url)

        register_jobs(self.scheduler, self)
        self.scheduler.start()

        sources = [p.title for p in self.sync_service.providers]
        checkers = [c.name for c in self.scanner.checkers]
        log.info("app.started", sources=sources, checkers=checkers)

        await self._set_commands()
        await self.notifier.notify_text(render_startup(sources, checkers))
        try:
            await self.dp.start_polling(self.bot, handle_signals=True)
        finally:
            self.scheduler.shutdown(wait=False)
            with contextlib.suppress(Exception):
                await self.bot.session.close()
            await shutdown_engine()
            log.info("app.stopped")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    asyncio.run(Application(settings).run())


if __name__ == "__main__":
    main()
