"""Sync reconciliation against a real PostgreSQL.

Skipped unless TEST_DATABASE_URL points at a migrated, disposable database, e.g.
  TEST_DATABASE_URL=postgresql+asyncpg://user:pass@127.0.0.1:5432/domain_scanner_test
The tables are wiped before every test.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import select, text

from domain_scanner.db import init_engine, session_scope, shutdown_engine
from domain_scanner.db.models import Domain, DomainSource, SyncLog
from domain_scanner.services.sync import DomainSyncService
from domain_scanner.sources import SourceDomain

DB_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")


class FakeProvider:
    def __init__(self, source: DomainSource, title: str, domains: list[SourceDomain]):
        self.source = source
        self.title = title
        self.domains = domains
        self.fail: Exception | None = None

    async def fetch_domains(self) -> list[SourceDomain]:
        if self.fail:
            raise self.fail
        return list(self.domains)


def sd(name: str, active: bool = True, **kw) -> SourceDomain:
    return SourceDomain(name=name, is_active=active, status="ACTIVE" if active else "X", **kw)


@pytest.fixture(autouse=True)
async def db():
    init_engine(DB_URL)
    async with session_scope() as s:
        await s.execute(text("TRUNCATE scan_checks, scans, domains, sync_logs RESTART IDENTITY"))
    yield
    await shutdown_engine()


async def _domains() -> dict[str, Domain]:
    async with session_scope() as s:
        return {d.name: d for d in (await s.scalars(select(Domain))).all()}


async def test_two_sources_create_their_own_domains():
    pwa = FakeProvider(DomainSource.PWA, "PWA.partners", [sd("a.com", external_id="u1")])
    uc = FakeProvider(DomainSource.UCLIENT, "UClient", [sd("b.com", external_parent_id="p1")])
    results = await DomainSyncService([pwa, uc]).run()

    assert [r.created for r in results] == [1, 1]
    got = await _domains()
    assert got["a.com"].source is DomainSource.PWA and got["a.com"].external_id == "u1"
    assert got["b.com"].source is DomainSource.UCLIENT
    assert got["b.com"].external_parent_id == "p1"


async def test_a_source_never_deactivates_the_other_sources_domains():
    pwa = FakeProvider(DomainSource.PWA, "PWA.partners", [sd("a.com")])
    uc = FakeProvider(DomainSource.UCLIENT, "UClient", [sd("b.com")])
    service = DomainSyncService([pwa, uc])
    await service.run()

    pwa.domains = []  # a.com disappears from PWA.partners
    results = await service.run()

    got = await _domains()
    assert got["a.com"].is_active is False
    assert got["b.com"].is_active is True  # untouched by the PWA sync
    assert results[0].deactivated == 1 and results[1].deactivated == 0


async def test_domain_owned_by_one_source_is_left_alone_by_another():
    pwa = FakeProvider(DomainSource.PWA, "PWA.partners", [sd("shared.com", external_id="u1")])
    uc = FakeProvider(DomainSource.UCLIENT, "UClient", [sd("shared.com", active=False)])
    results = await DomainSyncService([pwa, uc]).run()

    got = await _domains()
    assert got["shared.com"].source is DomainSource.PWA
    assert got["shared.com"].is_active is True
    assert results[1].foreign == 1


async def test_manual_domain_is_adopted_by_the_source_that_reports_it():
    async with session_scope() as s:
        s.add(Domain(name="m.com", source=DomainSource.MANUAL, is_active=True))
    uc = FakeProvider(DomainSource.UCLIENT, "UClient", [sd("m.com", external_parent_id="p9")])
    await DomainSyncService([uc]).run()

    got = await _domains()
    assert got["m.com"].source is DomainSource.UCLIENT
    assert got["m.com"].external_parent_id == "p9"


async def test_manual_domains_are_never_deactivated_by_a_sync():
    async with session_scope() as s:
        s.add(Domain(name="m.com", source=DomainSource.MANUAL, is_active=True))
    await DomainSyncService([FakeProvider(DomainSource.PWA, "PWA.partners", [])]).run()
    assert (await _domains())["m.com"].is_active is True


async def test_one_failing_source_does_not_stop_the_other():
    pwa = FakeProvider(DomainSource.PWA, "PWA.partners", [sd("a.com")])
    pwa.fail = RuntimeError("HTTP 500")
    uc = FakeProvider(DomainSource.UCLIENT, "UClient", [sd("b.com")])
    results = await DomainSyncService([pwa, uc]).run()

    assert not results[0].ok and "HTTP 500" in (results[0].error or "")
    assert results[1].ok and results[1].created == 1
    async with session_scope() as s:
        logs = (await s.scalars(select(SyncLog).order_by(SyncLog.id))).all()
    assert [(log.source, log.error is None) for log in logs] == [
        (DomainSource.PWA, False),
        (DomainSource.UCLIENT, True),
    ]


async def test_status_change_is_matched_by_external_id():
    pwa = FakeProvider(DomainSource.PWA, "PWA.partners", [sd("old.com", external_id="u1")])
    service = DomainSyncService([pwa])
    await service.run()
    pwa.domains = [sd("old.com", external_id="u1", active=False)]
    await service.run()
    got = await _domains()
    assert got["old.com"].is_active is False
    assert got["old.com"].external_status == "X"
