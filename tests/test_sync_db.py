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


def sd(name: str, status: str = "ACTIVE", **kw) -> SourceDomain:
    return SourceDomain(name=name, status=status, **kw)


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
    pwa = FakeProvider(DomainSource.PWA, "PWApartners", [sd("a.com", external_id="u1")])
    sk = FakeProvider(DomainSource.SKAKAPP, "SkakApp", [sd("b.com", external_parent_id="p1")])
    results = await DomainSyncService([pwa, sk]).run()

    assert [r.created for r in results] == [1, 1]
    got = await _domains()
    assert got["a.com"].source is DomainSource.PWA and got["a.com"].external_id == "u1"
    assert got["b.com"].source is DomainSource.SKAKAPP
    assert got["b.com"].external_parent_id == "p1"


async def test_every_reported_domain_is_checked_whatever_its_status():
    pwa = FakeProvider(
        DomainSource.PWA, "PWApartners", [sd("a.com", status="1"), sd("b.com", status="9")]
    )
    await DomainSyncService([pwa]).run()
    got = await _domains()
    assert got["a.com"].is_active and got["b.com"].is_active
    assert got["b.com"].external_status == "9"


async def test_a_source_only_drops_its_own_domains():
    pwa = FakeProvider(DomainSource.PWA, "PWApartners", [sd("a.com")])
    sk = FakeProvider(DomainSource.SKAKAPP, "SkakApp", [sd("b.com")])
    service = DomainSyncService([pwa, sk])
    await service.run()

    pwa.domains = []  # a.com disappears from PWApartners
    results = await service.run()

    got = await _domains()
    assert got["a.com"].is_active is False  # no longer checked
    assert got["b.com"].is_active is True  # untouched by the PWA sync
    assert results[0].removed == 1 and results[1].removed == 0


async def test_domain_that_comes_back_is_checked_again():
    pwa = FakeProvider(DomainSource.PWA, "PWApartners", [sd("a.com")])
    service = DomainSyncService([pwa])
    await service.run()
    pwa.domains = []
    await service.run()
    pwa.domains = [sd("a.com")]
    await service.run()
    assert (await _domains())["a.com"].is_active is True


async def test_domain_owned_by_one_source_is_left_alone_by_another():
    pwa = FakeProvider(DomainSource.PWA, "PWApartners", [sd("shared.com", external_id="u1")])
    sk = FakeProvider(DomainSource.SKAKAPP, "SkakApp", [sd("shared.com", status="DISABLE")])
    results = await DomainSyncService([pwa, sk]).run()

    got = await _domains()
    assert got["shared.com"].source is DomainSource.PWA
    assert got["shared.com"].is_active is True
    assert results[1].foreign == 1


async def test_domain_dropped_by_its_owner_is_taken_over_by_another_source():
    pwa = FakeProvider(DomainSource.PWA, "PWApartners", [sd("shared.com")])
    sk = FakeProvider(DomainSource.SKAKAPP, "SkakApp", [sd("shared.com", external_parent_id="p")])
    service = DomainSyncService([pwa, sk])
    await service.run()

    pwa.domains = []  # removed from PWApartners, still live in SkakApp
    results = await service.run()

    got = await _domains()
    assert got["shared.com"].source is DomainSource.SKAKAPP
    assert got["shared.com"].is_active is True  # keeps being checked
    assert results[1].foreign == 0 and results[1].updated == 1


async def test_manual_domain_is_adopted_by_the_source_that_reports_it():
    async with session_scope() as s:
        s.add(Domain(name="m.com", source=DomainSource.MANUAL, is_active=True))
    sk = FakeProvider(DomainSource.SKAKAPP, "SkakApp", [sd("m.com", external_parent_id="p9")])
    await DomainSyncService([sk]).run()

    got = await _domains()
    assert got["m.com"].source is DomainSource.SKAKAPP
    assert got["m.com"].external_parent_id == "p9"


async def test_manual_domains_are_never_deactivated_by_a_sync():
    async with session_scope() as s:
        s.add(Domain(name="m.com", source=DomainSource.MANUAL, is_active=True))
    await DomainSyncService([FakeProvider(DomainSource.PWA, "PWApartners", [])]).run()
    assert (await _domains())["m.com"].is_active is True


async def test_one_failing_source_does_not_stop_the_other():
    pwa = FakeProvider(DomainSource.PWA, "PWApartners", [sd("a.com")])
    pwa.fail = RuntimeError("HTTP 500")
    sk = FakeProvider(DomainSource.SKAKAPP, "SkakApp", [sd("b.com")])
    results = await DomainSyncService([pwa, sk]).run()

    assert not results[0].ok and "HTTP 500" in (results[0].error or "")
    assert results[1].ok and results[1].created == 1
    async with session_scope() as s:
        logs = (await s.scalars(select(SyncLog).order_by(SyncLog.id))).all()
    assert [(log.source, log.error is None) for log in logs] == [
        (DomainSource.PWA, False),
        (DomainSource.SKAKAPP, True),
    ]


async def test_status_change_is_matched_by_external_id():
    pwa = FakeProvider(DomainSource.PWA, "PWApartners", [sd("old.com", external_id="u1")])
    service = DomainSyncService([pwa])
    await service.run()
    pwa.domains = [sd("old.com", external_id="u1", status="9")]
    await service.run()
    got = await _domains()
    assert got["old.com"].is_active is True
    assert got["old.com"].external_status == "9"


async def test_mute_watched_only_touches_watched_problem_domains():
    from domain_scanner.db.models import Verdict
    from domain_scanner.repositories import DomainRepository

    async with session_scope() as s:
        s.add_all([
            Domain(name="flagged.com", current_verdict=Verdict.FLAGGED),
            Domain(name="suspicious.com", current_verdict=Verdict.SUSPICIOUS),
            Domain(name="clean.com", current_verdict=Verdict.CLEAN),
            Domain(name="already-muted.com", current_verdict=Verdict.FLAGGED,
                   monitoring_enabled=False),
            Domain(name="gone.com", current_verdict=Verdict.FLAGGED, is_active=False),
        ])
    async with session_scope() as s:
        muted = await DomainRepository(s).mute_watched({Verdict.FLAGGED, Verdict.SUSPICIOUS})

    assert muted == 2  # only the two watched problem domains
    got = await _domains()
    assert got["flagged.com"].monitoring_enabled is False
    assert got["suspicious.com"].monitoring_enabled is False
    assert got["clean.com"].monitoring_enabled is True
    assert got["gone.com"].monitoring_enabled is True  # not reported by its source

    async with session_scope() as s:
        again = await DomainRepository(s).mute_watched({Verdict.FLAGGED, Verdict.SUSPICIOUS})
    assert again == 0  # pressing the button twice changes nothing
