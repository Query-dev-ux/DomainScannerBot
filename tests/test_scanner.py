from __future__ import annotations

from domain_scanner.checkers.base import CheckOutcome
from domain_scanner.checkers.source_status import NOT_LIVE_SUMMARY
from domain_scanner.db.models import DomainSource, Verdict
from domain_scanner.services.scanner import (
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
