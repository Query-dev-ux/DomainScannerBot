from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from domain_scanner.bot.render import render_job_crash, render_sync_failure
from domain_scanner.logging import get_logger
from domain_scanner.services.scanner import (
    collect_monitored_domain_ids,
    scan_source_statuses,
)

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


# The sync runs often, so a source that stays down must not repeat itself every
# few minutes: the group hears about it when the state changes, not on a loop.
_source_was_ok: dict[str, bool] = {}


def _newly_failed(results: list) -> list:
    """Sources that were fine (or unseen) last run and are failing now."""
    fresh = []
    for r in results:
        if not r.ok and _source_was_ok.get(r.title, True):
            fresh.append(r)
        _source_was_ok[r.title] = r.ok
    return fresh


async def run_sync(app: Application) -> None:
    """Pull the domain lists, then act on the statuses that came with them.

    The platform's own status is the earliest signal we have -- a ban is in the
    API while DNS and Safe Browsing are still quiet -- and reading it costs no
    network call beyond this sync, so bans are reported here rather than waiting
    for the hourly scan.
    """
    log.info("job.sync.start")
    try:
        results = await app.sync_service.run()
    except Exception:
        log.exception("job.sync.error")
        await app.notifier.notify_text(render_job_crash("Синхронизация"))
        return
    # Each source is isolated: one failing does not stop the others, but the group
    # should know which one is down.
    failed = _newly_failed(list(results))
    if failed:
        await app.notifier.notify_text(render_sync_failure(failed))

    try:
        for report in await scan_source_statuses():
            await app.notifier.notify_scan(report)
    except Exception:
        log.exception("job.sync.status_error")


async def run_scan(app: Application) -> None:
    """Check every monitored domain."""
    log.info("job.scan.start")
    try:
        domain_ids = await collect_monitored_domain_ids()
        # Checks that run on a budget (Facebook) would otherwise always spend it
        # on the same head of the list; shuffling spreads the coverage.
        random.shuffle(domain_ids)
        reports = await app.scanner.scan_many(domain_ids)
        alerts = [r for r in reports if r.needs_alert]
        for report in alerts:
            await app.notifier.notify_scan(report)
        log.info("job.scan.done", scanned=len(reports), alerts=len(alerts))
    except Exception:
        log.exception("job.scan.error")
        await app.notifier.notify_text(render_job_crash("Плановая проверка"))


def register_jobs(scheduler: AsyncIOScheduler, app: Application) -> None:
    settings = app.settings
    now = datetime.now(UTC)

    scheduler.add_job(
        run_sync,
        "interval",
        minutes=settings.sync_interval_minutes,
        id=SYNC_JOB_ID,
        name="Синхронизация доменов",
        args=[app],
        max_instances=1,
        coalesce=True,
        misfire_grace_time=MISFIRE_GRACE_SECONDS,
        next_run_time=now + FIRST_SYNC_DELAY,
    )
    # A run that outlasts the interval is not started twice (max_instances=1);
    # the next one simply follows it.
    scheduler.add_job(
        run_scan,
        "interval",
        minutes=settings.scan_interval_minutes,
        id=SCAN_JOB_ID,
        name="Проверка всех доменов",
        args=[app],
        max_instances=1,
        coalesce=True,
        misfire_grace_time=MISFIRE_GRACE_SECONDS,
        next_run_time=now + FIRST_SCAN_DELAY,
    )

    for job in scheduler.get_jobs():
        log.info("job.registered", job_id=job.id, next_run_time=str(job.next_run_time))
