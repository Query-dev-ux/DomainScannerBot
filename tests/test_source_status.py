from __future__ import annotations

import pytest

from domain_scanner.checkers.source_status import check_source_status
from domain_scanner.db.models import DomainSource, Verdict


def test_banned_in_pwapartners_is_flagged():
    outcome = check_source_status(DomainSource.PWA, "9")
    assert outcome is not None
    assert outcome.verdict is Verdict.FLAGGED
    assert outcome.summary == "забанен в PWApartners"
    assert outcome.raw == {"status": "9"}


def test_expired_in_pwapartners_is_suspicious():
    outcome = check_source_status(DomainSource.PWA, "8")
    assert outcome is not None and outcome.verdict is Verdict.SUSPICIOUS


@pytest.mark.parametrize("status", ["1", "0", "2", "5", "6", "7", "10", "11", "", "junk"])
def test_other_pwapartners_statuses_say_nothing(status: str):
    assert check_source_status(DomainSource.PWA, status) is None


@pytest.mark.parametrize("status", ["ACTIVE", "DISABLE", "DISABLE_BALANCE", "ARCHIVE", "NEW"])
def test_skakapp_statuses_say_nothing_about_reputation(status: str):
    assert check_source_status(DomainSource.SKAKAPP, status) is None


def test_manual_domains_and_missing_status():
    assert check_source_status(DomainSource.MANUAL, "9") is None
    assert check_source_status(DomainSource.PWA, None) is None
    assert check_source_status(None, "9") is None
