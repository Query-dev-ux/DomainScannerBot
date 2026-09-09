from __future__ import annotations

from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from domain_scanner.logging import get_logger
from domain_scanner.services.scanner import collect_due_domain_ids

if TYPE_CHECKING:
    from domain_scanner.app import Application

log = get_logger(__name__)

SYNC_JOB_ID = "pwa-domain-sync"
SCAN_JOB_ID = "reputation-scan"


async def run_sync(app: Application) -> None:
    log.info("job.sync.start")
    try:
        await app.sync_service.run()
    except Exception:
        log.exception("job.sync.error")
        await app.notifier.notify_text("🛑 Синхронизация доменов с PWA API упала. См. логи.")


async def run_scan(app: Application) -> None:
    log.info("job.scan.start")
    try:
        domain_ids = await collect_due_domain_ids(app.settings.scan_interval_minutes)
        if not domain_ids:
            log.info("job.scan.nothing_due")
            return
        reports = await app.scanner.scan_many(domain_ids)
        alerts = [r for r in reports if r.needs_alert]
        for report in alerts:
            await app.notifier.notify_scan(report)
        log.info("job.scan.done", scanned=len(reports), alerts=len(alerts))
    except Exception:
        log.exception("job.scan.error")
        await app.notifier.notify_text("🛑 Плановое сканирование доменов упало. См. логи.")


def register_jobs(scheduler: AsyncIOScheduler, app: Application) -> None:
    settings = app.settings
    scheduler.add_job(
        run_sync,
        "interval",
        minutes=settings.sync_interval_minutes,
        id=SYNC_JOB_ID,
        args=[app],
        max_instances=1,
        coalesce=True,
        next_run_time=None,
    )
    scheduler.add_job(
        run_scan,
        "interval",
        minutes=max(1, settings.scan_interval_minutes // 3),
        id=SCAN_JOB_ID,
        args=[app],
        max_instances=1,
        coalesce=True,
        next_run_time=None,
    )
