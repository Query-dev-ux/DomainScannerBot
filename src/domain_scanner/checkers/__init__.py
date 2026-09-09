from __future__ import annotations

from domain_scanner.checkers.base import Checker, CheckOutcome
from domain_scanner.checkers.dns_rbl import DnsRblChecker
from domain_scanner.checkers.google_safe_browsing import GoogleSafeBrowsingChecker
from domain_scanner.checkers.virustotal import VirusTotalChecker
from domain_scanner.config import Settings

__all__ = [
    "CheckOutcome",
    "Checker",
    "DnsRblChecker",
    "GoogleSafeBrowsingChecker",
    "VirusTotalChecker",
    "build_checkers",
]


def build_checkers(settings: Settings) -> list[Checker]:
    """Instantiate every checker that is configured/enabled."""
    checkers: list[Checker] = [DnsRblChecker()]
    if settings.gsb_api_key:
        checkers.append(GoogleSafeBrowsingChecker(settings.gsb_api_key))
    if settings.virustotal_api_key:
        checkers.append(VirusTotalChecker(settings.virustotal_api_key))
    return checkers
