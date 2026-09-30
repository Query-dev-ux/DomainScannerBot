from __future__ import annotations

import pytest

from domain_scanner.checkers.source_status import check_source_status, is_not_live
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


def test_status_matching_ignores_case():
    # SkakApp's spec says ACTIVE but the live API answers "active" — never rely on
    # the documented casing when mapping a status.
    from domain_scanner.checkers.source_status import _BAD_STATUSES

    assert all(k == k.lower() for m in _BAD_STATUSES.values() for k in m)
    assert check_source_status(DomainSource.PWA, " 9 ") is not None


# ── домен ещё настраивается ──────────────────────────────────────────────────


@pytest.mark.parametrize("status", ["0", "2", "5", "6", "7", "10", "11"])
def test_pwapartners_setup_statuses_mean_the_domain_is_not_live(status: str):
    assert is_not_live(DomainSource.PWA, status)


@pytest.mark.parametrize("status", ["1", "8", "9", "", "junk", None])
def test_a_live_or_banned_domain_is_judged_as_usual(status: str | None):
    assert not is_not_live(DomainSource.PWA, status)


def test_skakapp_counts_a_disabled_domain_as_not_live():
    assert is_not_live(DomainSource.SKAKAPP, "disabled")
    assert not is_not_live(DomainSource.SKAKAPP, "ok")
    assert not is_not_live(DomainSource.SKAKAPP, "banned")
    assert not is_not_live(DomainSource.MANUAL, "0")
