from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from domain_scanner.db.models import DomainSource, Verdict
from domain_scanner.scheduler import jobs
from domain_scanner.scheduler.jobs import SCAN_JOB_ID, SYNC_JOB_ID, register_jobs
from domain_scanner.services.scanner import ScanReport
from domain_scanner.services.sync import SyncResult


@pytest.fixture
def app() -> SimpleNamespace:
    settings = SimpleNamespace(
        sync_interval_minutes=60,
        scan_interval_minutes=60,
    )
    return SimpleNamespace(settings=settings)


@pytest.fixture
def scheduler() -> AsyncIOScheduler:
    return AsyncIOScheduler(timezone="UTC")


def test_both_jobs_are_registered(scheduler, app):
    register_jobs(scheduler, app)
    assert {j.id for j in scheduler.get_jobs()} == {SYNC_JOB_ID, SCAN_JOB_ID}


def test_jobs_are_not_paused(scheduler, app):
    """Regression: passing next_run_time=None to add_job creates a PAUSED job
    that silently never fires — which is how the scheduler shipped originally."""
    register_jobs(scheduler, app)
    for job in scheduler.get_jobs():
        assert job.next_run_time is not None, f"{job.id} registered paused"


def test_first_runs_are_soon_and_sync_goes_first(scheduler, app):
    register_jobs(scheduler, app)
    jobs = {j.id: j for j in scheduler.get_jobs()}
    now = datetime.now(UTC)

    sync_at = jobs[SYNC_JOB_ID].next_run_time
    scan_at = jobs[SCAN_JOB_ID].next_run_time

    # Both within minutes of boot, not a full interval away.
    assert sync_at - now < timedelta(minutes=5)
    assert scan_at - now < timedelta(minutes=5)
    # Sync must populate domains before the first scan looks for them.
    assert sync_at < scan_at


def test_jobs_do_not_overlap_and_survive_downtime(scheduler, app):
    register_jobs(scheduler, app)
    for job in scheduler.get_jobs():
        assert job.max_instances == 1
        assert job.coalesce is True
        assert job.misfire_grace_time and job.misfire_grace_time > 0


def test_scan_runs_every_scan_interval(scheduler, app):
    register_jobs(scheduler, app)
    scan = scheduler.get_job(SCAN_JOB_ID)
    assert scan.trigger.interval == timedelta(minutes=app.settings.scan_interval_minutes)


# ── статусы платформы приезжают вместе с выгрузкой ───────────────────────────


class Recorder:
    """Stands in for the notifier: remembers what would have gone to Telegram."""

    chat_id = -1

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.reports: list[object] = []

    async def notify_text(self, text: str) -> None:
        self.texts.append(text)

    async def notify_scan(self, report) -> None:
        self.reports.append(report)


def _result(title: str, *, ok: bool):
    return SyncResult(DomainSource.PWA, title, error=None if ok else "HTTP 500")


def _app(notifier, results):
    sync_service = SimpleNamespace(run=lambda: _done(results))
    return SimpleNamespace(notifier=notifier, sync_service=sync_service)


async def _done(value):
    return value


@pytest.fixture(autouse=True)
def forget_source_state():
    jobs._source_was_ok.clear()
    yield
    jobs._source_was_ok.clear()


async def test_a_new_ban_is_reported_right_after_the_sync(monkeypatch):
    # The whole point of syncing often: a ban reaches the group without waiting
    # for the hourly scan.
    report = ScanReport("bad.com", Verdict.FLAGGED, Verdict.CLEAN, changed=True)

    async def fake_statuses():
        return [report]

    monkeypatch.setattr(jobs, "scan_source_statuses", fake_statuses)
    notifier = Recorder()
    await jobs.run_sync(_app(notifier, [_result("PWApartners", ok=True)]))
    assert notifier.reports == [report]
    assert notifier.texts == []


async def test_a_source_that_stays_down_is_reported_once(monkeypatch):
    async def no_statuses():
        return []

    monkeypatch.setattr(jobs, "scan_source_statuses", no_statuses)
    notifier = Recorder()
    app = _app(notifier, [_result("PWApartners", ok=False)])

    await jobs.run_sync(app)
    await jobs.run_sync(app)
    await jobs.run_sync(app)
    # Once, not once every ten minutes.
    assert len(notifier.texts) == 1
    assert "PWApartners" in notifier.texts[0]

    # Recovered, then broken again — that is news.
    await jobs.run_sync(_app(notifier, [_result("PWApartners", ok=True)]))
    await jobs.run_sync(app)
    assert len(notifier.texts) == 2


async def test_a_broken_status_pass_does_not_kill_the_sync(monkeypatch):
    async def boom():
        raise RuntimeError("no database")

    monkeypatch.setattr(jobs, "scan_source_statuses", boom)
    notifier = Recorder()
    await jobs.run_sync(_app(notifier, [_result("PWApartners", ok=True)]))
    assert notifier.texts == [] and notifier.reports == []
