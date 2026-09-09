from __future__ import annotations

from domain_scanner.checkers.base import CheckOutcome
from domain_scanner.db.models import Verdict
from domain_scanner.services.scanner import ScanReport, aggregate_verdict


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
