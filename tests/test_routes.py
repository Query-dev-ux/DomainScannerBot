"""Routing alerts to the group of whoever owns the domain."""

from __future__ import annotations

from dataclasses import dataclass

from domain_scanner.bot import render
from domain_scanner.bot.handlers.routes import parse_route
from domain_scanner.bot.notifier import Notifier
from domain_scanner.db.models import DomainSource, Verdict
from domain_scanner.services.scanner import ScanReport

DEFAULT_CHAT = -1001110000000
TEAM_CHAT = -1002220000000


@dataclass
class FakeRoute:
    source: DomainSource
    owner: str
    chat_id: int
    chat_title: str | None = None


def _report(**kw) -> ScanReport:
    base = dict(
        domain="example.com",
        verdict=Verdict.FLAGGED,
        previous_verdict=Verdict.CLEAN,
        changed=True,
        source=DomainSource.PWA,
        owner="CG_Rustam",
    )
    base.update(kw)
    return ScanReport(**base)


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, int | None]] = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, kw.get("message_thread_id")))


# ── разбор команды ───────────────────────────────────────────────────────────


def test_route_takes_a_chat_and_a_name_per_platform():
    request = parse_route("-1002220000000 CG_Rustam rustam_celestial")
    assert request is not None
    assert request.chat_id == TEAM_CHAT
    assert request.owners == {
        DomainSource.PWA: "CG_Rustam",
        DomainSource.SKAKAPP: "rustam_celestial",
    }


def test_a_dash_means_the_person_has_no_account_there():
    request = parse_route("-1002220000000 CG_Oleg -")
    assert request is not None
    assert request.owners == {DomainSource.PWA: "CG_Oleg"}
    assert parse_route("-1002220000000 - luka_celestial").owners == {
        DomainSource.SKAKAPP: "luka_celestial"
    }


def test_garbage_is_refused_rather_than_guessed():
    assert parse_route(None) is None
    assert parse_route("") is None
    assert parse_route("-1002220000000 CG_Rustam") is None          # a name missing
    assert parse_route("группа CG_Rustam rustam") is None           # not a chat id
    assert parse_route("-1002220000000 - -") is None                # no names at all
    assert parse_route("-100222 a b c") is None                     # too many words


# ── куда уходит алерт ────────────────────────────────────────────────────────


async def test_alert_goes_to_the_owners_group():
    bot = FakeBot()

    async def lookup(report: ScanReport) -> int | None:
        return TEAM_CHAT if report.owner == "CG_Rustam" else None

    notifier = Notifier(bot, DEFAULT_CHAT, 42, route_lookup=lookup)
    await notifier.notify_scan(_report())
    # The forum topic belongs to the default group, so a routed alert has none.
    assert bot.sent == [(TEAM_CHAT, None)]


async def test_without_a_rule_the_alert_goes_to_the_default_group():
    bot = FakeBot()

    async def lookup(report: ScanReport) -> int | None:
        return None

    notifier = Notifier(bot, DEFAULT_CHAT, 42, route_lookup=lookup)
    await notifier.notify_scan(_report(owner=None))
    assert bot.sent == [(DEFAULT_CHAT, 42)]


async def test_a_broken_routing_table_still_delivers_the_alert():
    bot = FakeBot()

    async def lookup(report: ScanReport) -> int | None:
        raise RuntimeError("no database")

    notifier = Notifier(bot, DEFAULT_CHAT, None, route_lookup=lookup)
    await notifier.notify_scan(_report())
    assert bot.sent == [(DEFAULT_CHAT, None)]


async def test_sync_failures_are_never_routed():
    bot = FakeBot()

    async def lookup(report: ScanReport) -> int | None:
        return TEAM_CHAT

    notifier = Notifier(bot, DEFAULT_CHAT, None, route_lookup=lookup)
    await notifier.notify_text("Синхронизация не удалась")
    assert bot.sent == [(DEFAULT_CHAT, None)]


# ── как это выглядит ─────────────────────────────────────────────────────────


def test_routes_are_listed_by_platform():
    routes = [
        FakeRoute(DomainSource.PWA, "CG_Rustam", TEAM_CHAT, "Celestial | Rustam"),
        FakeRoute(DomainSource.SKAKAPP, "rustam_celestial", TEAM_CHAT, None),
    ]
    text = render.render_routes(routes, DEFAULT_CHAT)
    assert text.split("\n") == [
        f"<b>{render.ROUTES_TITLE}</b>",
        "",
        "<blockquote>PWApartners</blockquote>",
        "  CG_Rustam → Celestial | Rustam",
        "<blockquote>SkakApp</blockquote>",
        f"  rustam_celestial → <code>{TEAM_CHAT}</code>",
        "",
        f"<i>Остальные — в основную группу, <code>{DEFAULT_CHAT}</code></i>",
    ]


def test_no_rules_yet_explains_how_to_add_one():
    text = render.render_routes([], DEFAULT_CHAT)
    assert "основную группу" in text
    assert "/route" in text


def test_route_messages_carry_no_emoji():
    for text in (
        render.render_route_usage(),
        render.render_route_unreachable(TEAM_CHAT),
        render.render_route_unknown("CG_Rustam"),
        render.render_routes([FakeRoute(DomainSource.PWA, "x", 1)], DEFAULT_CHAT),
    ):
        assert all(ord(ch) < 0x1F000 for ch in text), text
