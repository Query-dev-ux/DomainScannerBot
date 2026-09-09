from __future__ import annotations

import aiohttp

from domain_scanner.checkers.base import CheckOutcome
from domain_scanner.db.models import Verdict

_ENDPOINT = "https://safebrowsing.googleapis.com/v4/threatMatches:find"

_THREAT_TYPES = [
    "MALWARE",
    "SOCIAL_ENGINEERING",
    "UNWANTED_SOFTWARE",
    "POTENTIALLY_HARMFUL_APPLICATION",
]


class GoogleSafeBrowsingChecker:
    """Checks a domain against the Google Safe Browsing v4 lists."""

    name = "google_safe_browsing"

    def __init__(self, api_key: str, *, timeout: float = 20.0) -> None:
        self._api_key = api_key
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    def _payload(self, domain: str) -> dict:
        urls = [domain, f"http://{domain}/", f"https://{domain}/", f"http://www.{domain}/"]
        return {
            "client": {"clientId": "domain-scanner-bot", "clientVersion": "0.1.0"},
            "threatInfo": {
                "threatTypes": _THREAT_TYPES,
                "platformTypes": ["ANY_PLATFORM"],
                "threatEntryTypes": ["URL"],
                "threatEntries": [{"url": u} for u in urls],
            },
        }

    async def check(self, domain: str) -> CheckOutcome:
        params = {"key": self._api_key}
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.post(
                    _ENDPOINT, params=params, json=self._payload(domain)
                ) as resp:
                    text = await resp.text()
                    if resp.status != 200:
                        return CheckOutcome.failure(
                            self.name, f"HTTP {resp.status}: {text[:300]}"
                        )
                    data = await resp.json()
        except aiohttp.ClientError as exc:
            return CheckOutcome.failure(self.name, f"request failed: {exc}")

        matches = data.get("matches") or []
        if not matches:
            return CheckOutcome(
                checker=self.name, verdict=Verdict.CLEAN, summary="нет совпадений в GSB"
            )

        threats = sorted({m.get("threatType", "UNKNOWN") for m in matches})
        return CheckOutcome(
            checker=self.name,
            verdict=Verdict.FLAGGED,
            summary=f"GSB: {', '.join(threats)}",
            raw={"matches": matches},
        )
