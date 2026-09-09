from __future__ import annotations

import asyncio
import contextlib

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from domain_scanner.bot import build_dispatcher
from domain_scanner.checkers import build_checkers
from domain_scanner.clients.pwa_partners import PwaPartnersClient
from domain_scanner.config import Settings, get_settings
from domain_scanner.db import init_engine, shutdown_engine
from domain_scanner.logging import configure_logging, get_logger
from domain_scanner.scheduler import register_jobs
from domain_scanner.services import DomainSyncService, Notifier, ScannerService

log = get_logger(__name__)


class Application:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bot = Bot(
            token=settings.bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self.notifier = Notifier(
            self.bot, settings.alert_chat_id, settings.alert_thread_id
        )
        self.scanner = ScannerService(
            build_checkers(settings), concurrency=settings.scan_concurrency
        )
        self.sync_service = DomainSyncService(self._pwa_client_factory)
        self.scheduler = AsyncIOScheduler(timezone="UTC")
        self.dp = build_dispatcher(self)

    def _pwa_client_factory(self) -> PwaPartnersClient:
        s = self.settings
        return PwaPartnersClient(
            base_url=s.pwa_api_base_url,
            api_key=s.pwa_api_key,
            team_uuid=s.pwa_team_uuid,
            teamate_uuid=s.pwa_teamate_uuid,
        )

    async def run(self) -> None:
        init_engine(self.settings.database_url)

        register_jobs(self.scheduler, self)
        self.scheduler.start()
        log.info("app.started", checkers=[c.name for c in self.scanner.checkers])

        await self.notifier.notify_text("🟢 DomainScannerBot запущен.")
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
