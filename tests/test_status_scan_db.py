"""The status-only pass that runs after every sync, against a real PostgreSQL.

Skipped unless TEST_DATABASE_URL points at a migrated, disposable database —
see tests/test_sync_db.py for the format.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import func, select, text

from domain_scanner.db import init_engine, session_scope, shutdown_engine
from domain_scanner.db.models import Domain, DomainSource, Scan, Verdict
from domain_scanner.services.scanner import scan_source_statuses

DB_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture(autouse=True)
async def db():
    init_engine(DB_URL)
    async with session_scope() as s:
        await s.execute(text("TRUNCATE scan_checks, scans, domains RESTART IDENTITY CASCADE"))
    yield
    await shutdown_engine()


async def _add(name: str, status: str, verdict: Verdict, *, watched: bool = True) -> int:
    async with session_scope() as s:
        domain = Domain(
            name=name,
            source=DomainSource.PWA,
            external_status=status,
            current_verdict=verdict,
            is_active=True,
            monitoring_enabled=watched,
        )
        s.add(domain)
        await s.flush()
        return domain.id


async def _verdict(domain_id: int) -> Verdict:
    async with session_scope() as s:
        return (await s.get(Domain, domain_id)).current_verdict


async def _scans(domain_id: int) -> int:
    async with session_scope() as s:
        counted = select(func.count()).select_from(Scan).where(Scan.domain_id == domain_id)
        return int(await s.scalar(counted) or 0)


async def test_a_fresh_ban_is_caught_without_a_full_scan():
    banned = await _add("banned.com", "9", Verdict.CLEAN)

    reports = await scan_source_statuses()

    assert [(r.domain, r.verdict, r.previous_verdict) for r in reports] == [
        ("banned.com", Verdict.FLAGGED, Verdict.CLEAN)
    ]
    assert reports[0].needs_alert
    assert await _verdict(banned) is Verdict.FLAGGED
    assert await _scans(banned) == 1


async def test_it_reports_a_ban_once():
    await _add("banned.com", "9", Verdict.CLEAN)
    assert len(await scan_source_statuses()) == 1
    # The next sync ten minutes later must not repeat it.
    assert await scan_source_statuses() == []


async def test_a_clean_status_never_undoes_what_a_real_scan_found():
    # DNS or a blocklist flagged this one; the platform is happy with it. A status
    # pass knows nothing about blocklists, so it must leave the verdict alone.
    flagged = await _add("listed.com", "1", Verdict.FLAGGED)
    assert await scan_source_statuses() == []
    assert await _verdict(flagged) is Verdict.FLAGGED
    assert await _scans(flagged) == 0


async def test_a_milder_status_does_not_lower_the_verdict():
    # "просрочен" is only suspicious, and the domain is already flagged.
    expired = await _add("expired.com", "8", Verdict.FLAGGED)
    assert await scan_source_statuses() == []
    assert await _verdict(expired) is Verdict.FLAGGED


async def test_muted_domains_are_left_out():
    muted = await _add("muted.com", "9", Verdict.CLEAN, watched=False)
    assert await scan_source_statuses() == []
    assert await _verdict(muted) is Verdict.CLEAN
