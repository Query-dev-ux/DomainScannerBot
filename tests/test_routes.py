"""Routing alerts to the group of whoever owns the domain."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError

from domain_scanner.bot import notifier as notifier_module
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
    def __init__(self, fail_times: int = 0, error: Exception | None = None) -> None:
        self.sent: list[tuple[int, int | None]] = []
        self.attempts = 0
        self._fail_times = fail_times
        self._error = error or TelegramNetworkError(method=None, message="reset by peer")

    async def send_message(self, chat_id, text, **kw):
        self.attempts += 1
        if self.attempts <= self._fail_times:
            raise self._error
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


async def test_alert_goes_to_the_owners_group_and_is_copied_to_the_main_one():
    bot = FakeBot()

    async def lookup(report: ScanReport) -> int | None:
        return TEAM_CHAT if report.owner == "CG_Rustam" else None

    notifier = Notifier(bot, DEFAULT_CHAT, 42, route_lookup=lookup)
    await notifier.notify_scan(_report())
    # The forum topic belongs to the default group, so the routed copy has none.
    assert bot.sent == [(TEAM_CHAT, None), (DEFAULT_CHAT, 42)]


async def test_a_rule_pointing_at_the_main_group_does_not_double_the_alert():
    bot = FakeBot()

    async def lookup(report: ScanReport) -> int | None:
        return DEFAULT_CHAT

    notifier = Notifier(bot, DEFAULT_CHAT, 42, route_lookup=lookup)
    await notifier.notify_scan(_report())
    assert bot.sent == [(DEFAULT_CHAT, 42)]


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


# ── обрыв связи с Telegram ───────────────────────────────────────────────────


@pytest.fixture
def no_waiting(monkeypatch):
    """Take the backoff out of the test, keep the number of attempts."""
    waited: list[float] = []

    async def fake_sleep(delay: float) -> None:
        waited.append(delay)

    monkeypatch.setattr(notifier_module.asyncio, "sleep", fake_sleep)
    return waited


async def test_a_dropped_connection_is_retried(no_waiting):
    # api.telegram.org resets connections now and then; the alert must survive it.
    bot = FakeBot(fail_times=2)
    await Notifier(bot, DEFAULT_CHAT).notify_text("Домен зашкварен")
    assert bot.attempts == 3
    assert bot.sent == [(DEFAULT_CHAT, None)]
    assert no_waiting == list(notifier_module.SEND_BACKOFF)


async def test_retries_do_not_go_on_forever(no_waiting):
    bot = FakeBot(fail_times=99)
    await Notifier(bot, DEFAULT_CHAT).notify_text("Домен зашкварен")
    assert bot.attempts == len(notifier_module.SEND_BACKOFF) + 1
    assert bot.sent == []


async def test_a_rejected_message_is_not_retried(no_waiting):
    # Removed from the chat, wrong id, broken HTML — the next attempt fails too.
    bot = FakeBot(fail_times=99, error=TelegramBadRequest(method=None, message="chat not found"))
    await Notifier(bot, DEFAULT_CHAT).notify_text("Домен зашкварен")
    assert bot.attempts == 1
    assert no_waiting == []


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
        f"<i>Копии всех алертов — в основную группу, <code>{DEFAULT_CHAT}</code></i>",
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


# ── отметка о доставке ───────────────────────────────────────────────────────


@pytest.fixture
def marked(monkeypatch):
    """Which scans were written down as delivered."""
    seen: list[int] = []

    async def fake_mark(scan_id: int) -> None:
        seen.append(scan_id)

    monkeypatch.setattr(notifier_module, "mark_alert_sent", fake_mark)
    return seen


async def test_a_delivered_alert_is_written_down(marked, no_waiting):
    bot = FakeBot()
    await Notifier(bot, DEFAULT_CHAT).notify_scan(_report(scan_id=77))
    assert marked == [77]


async def test_an_undelivered_alert_is_not(marked, no_waiting):
    bot = FakeBot(fail_times=99)
    await Notifier(bot, DEFAULT_CHAT).notify_scan(_report(scan_id=77))
    assert bot.sent == []
    assert marked == []


async def test_reaching_one_group_of_two_still_counts(marked, no_waiting):
    # The owner's group is gone but the main one got it: the group was told.
    async def lookup(report):
        return TEAM_CHAT

    class OnlyMainChat(FakeBot):
        async def send_message(self, chat_id, text, **kw):
            if chat_id == TEAM_CHAT:
                raise TelegramBadRequest(method=None, message="chat not found")
            return await super().send_message(chat_id, text, **kw)

    bot = OnlyMainChat()
    await Notifier(bot, DEFAULT_CHAT, route_lookup=lookup).notify_scan(_report(scan_id=5))
    assert bot.sent == [(DEFAULT_CHAT, None)]
    assert marked == [5]
