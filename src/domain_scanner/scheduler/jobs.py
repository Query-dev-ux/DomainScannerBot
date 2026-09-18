from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from domain_scanner.bot.render import render_job_crash, render_sync_failure
from domain_scanner.logging import get_logger
from domain_scanner.services.scanner import collect_due_domain_ids, count_due_domains

if TYPE_CHECKING:
    from domain_scanner.app import Application

log = get_logger(__name__)

SYNC_JOB_ID = "pwa-domain-sync"
SCAN_JOB_ID = "reputation-scan"

# Delay before the first run after boot: sync first so the scan has domains to
# work with, rather than waiting a full interval on a fresh deploy.
FIRST_SYNC_DELAY = timedelta(seconds=30)
FIRST_SCAN_DELAY = timedelta(seconds=120)

# If the bot was down when a run was due, still run it when it comes back — as
# long as we are not more than this far past the scheduled time.
MISFIRE_GRACE_SECONDS = 600


async def run_sync(app: Application) -> None:
    log.info("job.sync.start")
    try:
        results = await app.sync_service.run()
    except Exception:
        log.exception("job.sync.error")
        await app.notifier.notify_text(render_job_crash("Синхронизация"))
        return
    # Each source is isolated: one failing does not stop the others, but the group
    # should know which one is down.
    if any(not r.ok for r in results):
        await app.notifier.notify_text(render_sync_failure(results))


async def run_scan(app: Application) -> None:
    batch_size = app.settings.scan_batch_size
    interval = app.settings.scan_interval_minutes
    log.info("job.scan.start", batch_size=batch_size)
    try:
        domain_ids = await collect_due_domain_ids(interval, limit=batch_size)
        if not domain_ids:
            log.info("job.scan.nothing_due")
            return
        reports = await app.scanner.scan_many(domain_ids)
        alerts = [r for r in reports if r.needs_alert]
        for report in alerts:
            await app.notifier.notify_scan(report)
        remaining = await count_due_domains(interval)
        log.info(
            "job.scan.done", scanned=len(reports), alerts=len(alerts), remaining=remaining
        )
    except Exception:
        log.exception("job.scan.error")
        await app.notifier.notify_text(render_job_crash("Плановая проверка"))


def scan_tick_minutes(scan_interval_minutes: int) -> int:
    """How often the scan job wakes up.

    Runs more often than the per-domain interval so domains become due in a
    rolling fashion instead of all at once, and so a batched backlog drains
    within one interval.
    """
    return max(1, scan_interval_minutes // 3)


def register_jobs(scheduler: AsyncIOScheduler, app: Application) -> None:
    settings = app.settings
    now = datetime.now(UTC)

    scheduler.add_job(
        run_sync,
        "interval",
        minutes=settings.sync_interval_minutes,
        id=SYNC_JOB_ID,
        name="Синхронизация доменов из PWA API",
        args=[app],
        max_instances=1,
        coalesce=True,
        misfire_grace_time=MISFIRE_GRACE_SECONDS,
        next_run_time=now + FIRST_SYNC_DELAY,
    )
    scheduler.add_job(
        run_scan,
        "interval",
        minutes=scan_tick_minutes(settings.scan_interval_minutes),
        id=SCAN_JOB_ID,
        name="Проверка репутации доменов",
        args=[app],
        max_instances=1,
        coalesce=True,
        misfire_grace_time=MISFIRE_GRACE_SECONDS,
        next_run_time=now + FIRST_SCAN_DELAY,
    )

    for job in scheduler.get_jobs():
        log.info("job.registered", job_id=job.id, next_run_time=str(job.next_run_time))
