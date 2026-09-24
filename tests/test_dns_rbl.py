from __future__ import annotations

import socket

import pytest

from domain_scanner.checkers.dns_rbl import DnsRblChecker
from domain_scanner.db.models import Verdict


class FakeResolver:
    """Stands in for getaddrinfo: each call pops the next scripted outcome."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def __call__(self, host, port, **kw):
        self.calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else self.outcomes
        if isinstance(outcome, Exception):
            raise outcome
        return [(None, None, None, None, (addr, 0)) for addr in outcome]


def _patch(monkeypatch, resolver):
    import asyncio

    loop = asyncio.get_event_loop_policy().new_event_loop()
    monkeypatch.setattr(loop, "getaddrinfo", resolver, raising=False)
    monkeypatch.setattr(
        "domain_scanner.checkers.dns_rbl.asyncio.get_running_loop", lambda: loop
    )
    return loop


def _gaierror(name: str) -> socket.gaierror:
    code = getattr(socket, name, -2)
    return socket.gaierror(code, name)


async def test_temporary_dns_failure_is_not_a_verdict(monkeypatch):
    # Three "try again" answers: the resolver is unhappy, the domain may be fine.
    resolver = FakeResolver(_gaierror("EAI_AGAIN"), _gaierror("EAI_AGAIN"), _gaierror("EAI_AGAIN"))
    _patch(monkeypatch, resolver)
    outcome = await DnsRblChecker(timeout=0.1).check("example.com")
    assert outcome.verdict is Verdict.ERROR
    assert outcome.error == "DNS не ответил"
    assert resolver.calls == 3  # retried before giving up


async def test_timeout_is_not_a_verdict_either(monkeypatch):
    resolver = FakeResolver(TimeoutError(), TimeoutError(), TimeoutError())
    _patch(monkeypatch, resolver)
    outcome = await DnsRblChecker(timeout=0.1).check("example.com")
    assert outcome.verdict is Verdict.ERROR


async def test_a_hiccup_then_an_answer_is_clean(monkeypatch):
    resolver = FakeResolver(_gaierror("EAI_AGAIN"), ["1.2.3.4"], [], [])
    _patch(monkeypatch, resolver)
    outcome = await DnsRblChecker(timeout=0.1).check("example.com")
    assert outcome.verdict is Verdict.CLEAN


async def test_definitive_nxdomain_is_suspicious_without_retrying(monkeypatch):
    resolver = FakeResolver(_gaierror("EAI_NONAME"))
    _patch(monkeypatch, resolver)
    outcome = await DnsRblChecker(timeout=0.1).check("gone.example")
    assert outcome.verdict is Verdict.SUSPICIOUS
    assert "не резолвится" in (outcome.summary or "")
    assert resolver.calls == 1  # no point asking again


async def test_blocklist_hit_is_flagged(monkeypatch):
    resolver = FakeResolver(["1.2.3.4"], ["127.0.0.72"], [])
    _patch(monkeypatch, resolver)
    outcome = await DnsRblChecker(timeout=0.1).check("bad.example")
    assert outcome.verdict is Verdict.FLAGGED
    assert "127.0.0.72" in (outcome.summary or "")


@pytest.mark.parametrize("addresses", [["127.255.255.254"], ["1.2.3.4"]])
async def test_non_listing_answers_from_a_blocklist_are_ignored(monkeypatch, addresses):
    # 127.255.255.x means the blocklist refused the query; anything outside
    # 127/8 is not a listing answer at all.
    resolver = FakeResolver(["1.2.3.4"], addresses, addresses)
    _patch(monkeypatch, resolver)
    outcome = await DnsRblChecker(timeout=0.1).check("example.com")
    assert outcome.verdict is Verdict.CLEAN
