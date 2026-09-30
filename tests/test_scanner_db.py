"""A whole-list scan against a real PostgreSQL.

Skipped unless TEST_DATABASE_URL points at a migrated, disposable database —
see tests/test_sync_db.py for the format.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import select, text

from domain_scanner.checkers.base import CheckOutcome
from domain_scanner.db import init_engine, session_scope, shutdown_engine
from domain_scanner.db.models import Domain, DomainSource, Verdict
from domain_scanner.services.scanner import ScannerService

DB_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")


class FakeBatch:
    """Stands in for Safe Browsing: judges every domain in one call."""

    name = "google_safe_browsing"

    def __init__(self) -> None:
        self.batches: list[list[str]] = []
        self.singles: list[str] = []

    async def check(self, domain: str) -> CheckOutcome:
        self.singles.append(domain)
        return CheckOutcome(checker=self.name, verdict=Verdict.CLEAN)

    async def check_many(self, domains: list[str]) -> dict[str, CheckOutcome]:
        self.batches.append(list(domains))
        return {
            d: CheckOutcome(
                checker=self.name,
                verdict=Verdict.FLAGGED if d.startswith("bad") else Verdict.CLEAN,
                summary="GSB: MALWARE" if d.startswith("bad") else None,
            )
            for d in domains
        }


@pytest.fixture(autouse=True)
async def db():
    init_engine(DB_URL)
    async with session_scope() as s:
        await s.execute(text("TRUNCATE scan_checks, scans, domains RESTART IDENTITY CASCADE"))
    yield
    await shutdown_engine()


async def _add(name: str) -> int:
    async with session_scope() as s:
        domain = Domain(name=name, source=DomainSource.PWA, external_status="1")
        s.add(domain)
        await s.flush()
        return domain.id


async def test_one_request_covers_the_whole_scan():
    ids = [await _add(n) for n in ("good.com", "bad.com", "other.com")]
    batch = FakeBatch()

    reports = await ScannerService([batch]).scan_many(ids)

    # Asked once about everything, never per domain — that is what keeps a
    # frequent scan inside the daily API quota.
    assert batch.batches == [["bad.com", "good.com", "other.com"]]
    assert batch.singles == []
    verdicts = {r.domain: r.verdict for r in reports}
    assert verdicts == {
        "good.com": Verdict.CLEAN,
        "bad.com": Verdict.FLAGGED,
        "other.com": Verdict.CLEAN,
    }
    # And each domain kept its own answer, not somebody else's.
    async with session_scope() as s:
        rows = (await s.scalars(select(Domain))).all()
        stored = {d.name: d.current_verdict for d in rows}
    assert stored["bad.com"] is Verdict.FLAGGED
    assert stored["good.com"] is Verdict.CLEAN

