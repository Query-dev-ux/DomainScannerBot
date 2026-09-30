"""Safe Browsing asked about many domains in one request."""

from __future__ import annotations

import pytest

from domain_scanner.checkers.base import BatchChecker, CheckOutcome
from domain_scanner.checkers.google_safe_browsing import (
    DOMAINS_PER_REQUEST,
    MAX_ENTRIES_PER_REQUEST,
    GoogleSafeBrowsingChecker,
    domain_of,
    urls_for,
)
from domain_scanner.db.models import Verdict


def _match(url: str, threat: str = "SOCIAL_ENGINEERING") -> dict:
    return {"threatType": threat, "threat": {"url": url}}


class FakeGsb(GoogleSafeBrowsingChecker):
    """The checker with the HTTP call replaced by canned answers."""

    def __init__(self, matches: list[dict] | None = None, error: Exception | None = None):
        super().__init__("key")
        self.batches: list[list[str]] = []
        self._matches = matches or []
        self._error = error

    async def check_many(self, domains):  # keeps the chunking, drops the session
        outcomes = {}
        unique = list(dict.fromkeys(domains))
        for start in range(0, len(unique), DOMAINS_PER_REQUEST):
            chunk = unique[start : start + DOMAINS_PER_REQUEST]
            self.batches.append(chunk)
            if self._error is not None:
                outcomes.update(
                    {d: CheckOutcome.failure(self.name, str(self._error)) for d in chunk}
                )
                continue
            wanted = {d for d in chunk}
            matches = [m for m in self._matches if domain_of(m["threat"]["url"]) in wanted]
            outcomes.update(self._outcomes(chunk, matches))
        return outcomes


def test_a_request_stays_inside_the_api_limit():
    payload = GoogleSafeBrowsingChecker("k")._payload(["a.com"] * DOMAINS_PER_REQUEST)
    entries = payload["threatInfo"]["threatEntries"]
    assert len(entries) <= MAX_ENTRIES_PER_REQUEST


def test_the_whole_list_costs_a_handful_of_requests():
    # 500 domains used to be 500 requests, over the 10k/day quota when scanned
    # hourly. Four is the point of the batch.
    domains = [f"d{i}.com" for i in range(500)]
    checker = FakeGsb()
    outcomes = _run(checker.check_many(domains))
    assert len(checker.batches) == 4
    assert len(outcomes) == 500
    assert all(o.verdict is Verdict.CLEAN for o in outcomes.values())


def test_a_listing_lands_on_its_own_domain():
    checker = FakeGsb([_match("http://bad.com/", "MALWARE")])
    outcomes = _run(checker.check_many(["good.com", "bad.com", "other.com"]))
    assert outcomes["bad.com"].verdict is Verdict.FLAGGED
    assert "MALWARE" in (outcomes["bad.com"].summary or "")
    assert outcomes["good.com"].verdict is Verdict.CLEAN
    assert outcomes["other.com"].verdict is Verdict.CLEAN


@pytest.mark.parametrize(
    "url",
    ["bad.com", "http://bad.com/", "https://bad.com/", "http://www.bad.com/path?x=1"],
)
def test_every_url_form_maps_back_to_the_domain(url: str):
    # We ask about four forms of each domain and Google may canonicalise them.
    assert domain_of(url) == "bad.com"
    assert all(domain_of(u) == "bad.com" for u in urls_for("bad.com"))


def test_a_match_we_cannot_attribute_is_not_pinned_on_someone_else():
    checker = FakeGsb()
    outcomes = checker._outcomes(["a.com"], [_match("http://stranger.com/")])
    assert outcomes["a.com"].verdict is Verdict.CLEAN


def test_a_failed_request_costs_only_its_own_domains():
    checker = FakeGsb(error=RuntimeError("HTTP 429: quota"))
    outcomes = _run(checker.check_many(["a.com", "b.com"]))
    assert all(o.verdict is Verdict.ERROR for o in outcomes.values())
    assert "429" in (outcomes["a.com"].error or "")


def test_a_single_domain_still_works_for_check():
    # /check and the recheck button go through the one-domain path.
    checker = FakeGsb([_match("http://bad.com/")])
    assert _run(checker.check("bad.com")).verdict is Verdict.FLAGGED
    assert _run(checker.check("good.com")).verdict is Verdict.CLEAN


def test_the_checker_is_recognised_as_batchable():
    # This is what makes the scanner ask it once instead of per domain.
    assert isinstance(GoogleSafeBrowsingChecker("k"), BatchChecker)


def _run(coro):
    import asyncio

    return asyncio.run(coro)
