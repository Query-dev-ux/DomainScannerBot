"""Alert routes against a real PostgreSQL.

Skipped unless TEST_DATABASE_URL points at a migrated, disposable database —
see tests/test_sync_db.py for the format.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from domain_scanner.db import init_engine, session_scope, shutdown_engine
from domain_scanner.db.models import DomainSource
from domain_scanner.repositories import AlertRouteRepository

DB_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")

TEAM_CHAT = -1002220000000
OTHER_CHAT = -1003330000000


@pytest.fixture(autouse=True)
async def db():
    init_engine(DB_URL)
    async with session_scope() as s:
        await s.execute(text("TRUNCATE alert_routes RESTART IDENTITY"))
    yield
    await shutdown_engine()


async def test_a_rule_sends_that_owners_domains_to_their_chat():
    async with session_scope() as s:
        repo = AlertRouteRepository(s)
        await repo.set_route(DomainSource.PWA, "CG_Rustam", TEAM_CHAT, "Rustam")
        await repo.set_route(DomainSource.SKAKAPP, "rustam_celestial", TEAM_CHAT)

    async with session_scope() as s:
        repo = AlertRouteRepository(s)
        assert await repo.chat_for(DomainSource.PWA, "CG_Rustam") == TEAM_CHAT
        # Logins are typed by hand, so the match ignores case.
        assert await repo.chat_for(DomainSource.PWA, "cg_rustam") == TEAM_CHAT
        assert await repo.chat_for(DomainSource.SKAKAPP, "rustam_celestial") == TEAM_CHAT
        # The same name in the other platform is a different person until routed.
        assert await repo.chat_for(DomainSource.SKAKAPP, "CG_Rustam") is None
        assert await repo.chat_for(DomainSource.PWA, "CG_Oleg") is None
        assert await repo.chat_for(DomainSource.PWA, None) is None
        assert await repo.chat_for(None, "CG_Rustam") is None


async def test_routing_the_same_owner_again_moves_them():
    async with session_scope() as s:
        repo = AlertRouteRepository(s)
        await repo.set_route(DomainSource.PWA, "CG_Rustam", TEAM_CHAT)
        await repo.set_route(DomainSource.PWA, "cg_rustam", OTHER_CHAT, "Другая")

    async with session_scope() as s:
        repo = AlertRouteRepository(s)
        routes = await repo.list_all()
        assert len(routes) == 1
        assert routes[0].chat_id == OTHER_CHAT
        assert routes[0].chat_title == "Другая"
        # The spelling follows the latest command.
        assert routes[0].owner == "cg_rustam"


async def test_deleting_a_name_clears_it_in_every_platform():
    async with session_scope() as s:
        repo = AlertRouteRepository(s)
        await repo.set_route(DomainSource.PWA, "CG_Oleg", TEAM_CHAT)
        await repo.set_route(DomainSource.SKAKAPP, "CG_Oleg", TEAM_CHAT)
        await repo.set_route(DomainSource.PWA, "CG_Luka", OTHER_CHAT)

    async with session_scope() as s:
        repo = AlertRouteRepository(s)
        assert await repo.delete_owner("cg_oleg") == 2
        assert await repo.delete_owner("cg_oleg") == 0

    async with session_scope() as s:
        routes = await AlertRouteRepository(s).list_all()
        assert [r.owner for r in routes] == ["CG_Luka"]
