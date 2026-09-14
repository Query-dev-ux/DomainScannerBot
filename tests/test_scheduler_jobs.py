from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from domain_scanner.scheduler.jobs import (
    SCAN_JOB_ID,
    SYNC_JOB_ID,
    register_jobs,
    scan_tick_minutes,
)


@pytest.fixture
def app() -> SimpleNamespace:
    settings = SimpleNamespace(
        sync_interval_minutes=60,
        scan_interval_minutes=180,
        scan_batch_size=50,
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


@pytest.mark.parametrize(
    ("interval", "expected"),
    [(180, 60), (60, 20), (3, 1), (1, 1), (2, 1)],
)
def test_scan_tick_is_a_fraction_of_the_interval(interval: int, expected: int):
    assert scan_tick_minutes(interval) == expected
