from __future__ import annotations

from domain_scanner.checkers.base import CheckOutcome
from domain_scanner.checkers.source_status import NOT_LIVE_SUMMARY
from domain_scanner.db.models import DomainSource, Verdict
from domain_scanner.services.scanner import (
    ScannerService,
    ScanReport,
    aggregate_verdict,
    excuse_missing_dns,
)


def _out(verdict: Verdict) -> CheckOutcome:
    return CheckOutcome(checker="t", verdict=verdict)


def test_aggregate_picks_worst_non_error():
    outcomes = [_out(Verdict.CLEAN), _out(Verdict.SUSPICIOUS), _out(Verdict.ERROR)]
    assert aggregate_verdict(outcomes) is Verdict.SUSPICIOUS


def test_aggregate_all_errors_is_error():
    assert aggregate_verdict([_out(Verdict.ERROR), _out(Verdict.ERROR)]) is Verdict.ERROR


def test_aggregate_flagged_wins():
    outcomes = [_out(Verdict.CLEAN), _out(Verdict.FLAGGED), _out(Verdict.SUSPICIOUS)]
    assert aggregate_verdict(outcomes) is Verdict.FLAGGED


def test_needs_alert_only_on_change_to_bad():
    clean_to_flagged = ScanReport("d", Verdict.FLAGGED, Verdict.CLEAN, changed=True)
    assert clean_to_flagged.needs_alert is True

    unchanged = ScanReport("d", Verdict.FLAGGED, Verdict.FLAGGED, changed=False)
    assert unchanged.needs_alert is False

    recovered = ScanReport("d", Verdict.CLEAN, Verdict.FLAGGED, changed=True)
    assert recovered.needs_alert is False


def test_source_ban_alone_makes_the_domain_flagged():
    from domain_scanner.checkers.source_status import check_source_status
    from domain_scanner.db.models import DomainSource

    ban = check_source_status(DomainSource.PWA, "9")
    assert ban is not None
    outcomes = [ban, _out(Verdict.CLEAN), _out(Verdict.CLEAN)]
    assert aggregate_verdict(outcomes) is Verdict.FLAGGED


# ── домен, который платформа ещё настраивает ─────────────────────────

# What DnsRblChecker returns for a domain that does not resolve; the marker is
# pinned to the checker itself in tests/test_dns_rbl.py.
NO_DNS = CheckOutcome(
    checker="dns_rbl",
    verdict=Verdict.SUSPICIOUS,
    summary="домен не резолвится (NXDOMAIN / нет A-записи)",
    raw={"resolves": False},
)


def test_a_domain_being_set_up_is_not_suspicious_for_having_no_dns():
    # "Настраивается" in PWApartners means the domain is not wired up yet, so it
    # cannot resolve -- alerting on that woke the group for every new purchase.
    outcome = excuse_missing_dns([NO_DNS], DomainSource.PWA, "5")[0]
    assert outcome.verdict is Verdict.UNKNOWN
    assert outcome.summary == NOT_LIVE_SUMMARY
    # Without DNS the blocklists were never asked, so this is "не проверен",
    # not "чисто".
    assert aggregate_verdict([outcome]) is Verdict.UNKNOWN
    assert not ScanReport("d", Verdict.UNKNOWN, Verdict.SUSPICIOUS, changed=True).needs_alert


def test_a_live_domain_that_stopped_resolving_is_still_suspicious():
    for status in ("1", "9", None):
        assert excuse_missing_dns([NO_DNS], DomainSource.PWA, status) == [NO_DNS]


def test_the_excuse_touches_nothing_else():
    listed = CheckOutcome(checker="dns_rbl", verdict=Verdict.FLAGGED, raw={"listed": {}})
    others = [listed, _out(Verdict.SUSPICIOUS)]
    assert excuse_missing_dns(others, DomainSource.PWA, "5") == others


# ── пакетные проверки ────────────────────────────────────────────────────────


class FakeDns:
    name = "dns_rbl"

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def check(self, domain: str) -> CheckOutcome:
        self.asked.append(domain)
        return CheckOutcome(checker=self.name, verdict=Verdict.CLEAN)


class FakeBatch:
    name = "google_safe_browsing"

    def __init__(self, error: Exception | None = None) -> None:
        self.batches: list[list[str]] = []
        self.singles: list[str] = []
        self._error = error

    async def check(self, domain: str) -> CheckOutcome:
        self.singles.append(domain)
        return CheckOutcome(checker=self.name, verdict=Verdict.CLEAN)

    async def check_many(self, domains: list[str]) -> dict[str, CheckOutcome]:
        self.batches.append(list(domains))
        if self._error is not None:
            raise self._error
        return {
            d: CheckOutcome(
                checker=self.name,
                verdict=Verdict.FLAGGED if d.startswith("bad") else Verdict.CLEAN,
            )
            for d in domains
        }


async def test_a_batch_checker_is_asked_once_for_the_whole_list():
    batch = FakeBatch()
    service = ScannerService([FakeDns(), batch])

    ready = await service._batch_outcomes(["a.com", "bad.com"])

    assert batch.batches == [["a.com", "bad.com"]]
    assert ready["bad.com"]["google_safe_browsing"].verdict is Verdict.FLAGGED
    assert ready["a.com"]["google_safe_browsing"].verdict is Verdict.CLEAN


async def test_a_domain_is_not_asked_again_about_what_the_batch_answered():
    dns, batch = FakeDns(), FakeBatch()
    service = ScannerService([dns, batch])

    ready = await service._batch_outcomes(["a.com"])
    outcomes = await service._run_checkers("a.com", ready["a.com"])

    assert batch.singles == []  # the whole point: one request, not one per domain
    assert dns.asked == ["a.com"]
    # Order follows the configured checkers, not who answered first.
    assert [o.checker for o in outcomes] == ["dns_rbl", "google_safe_browsing"]


async def test_a_batch_that_blows_up_costs_only_its_own_verdicts():
    batch = FakeBatch(error=RuntimeError("HTTP 429"))
    service = ScannerService([FakeDns(), batch])

    ready = await service._batch_outcomes(["a.com", "b.com"])

    assert all(r["google_safe_browsing"].verdict is Verdict.ERROR for r in ready.values())
    # An errored check never decides a verdict on its own.
    outcomes = await service._run_checkers("a.com", ready["a.com"])
    assert aggregate_verdict(outcomes) is Verdict.CLEAN
