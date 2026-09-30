"""Google Safe Browsing v4, asked about many domains at once.

The Lookup API allows 500 URL entries per request and, by default, 10 000
requests a day. One request per domain put an hourly scan of ~500 domains at
~12 000 requests — over the ceiling, after which the check quietly returns
errors for the rest of the day. Batching turns the same scan into a handful of
requests, so the interval is ours to choose.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import aiohttp

from domain_scanner.checkers.base import CheckOutcome
from domain_scanner.db.models import Verdict
from domain_scanner.logging import get_logger

log = get_logger(__name__)

_ENDPOINT = "https://safebrowsing.googleapis.com/v4/threatMatches:find"

_THREAT_TYPES = [
    "MALWARE",
    "SOCIAL_ENGINEERING",
    "UNWANTED_SOFTWARE",
    "POTENTIALLY_HARMFUL_APPLICATION",
]

# The forms of the same domain we ask about; a listing usually names just one.
_URL_FORMS = ("{d}", "http://{d}/", "https://{d}/", "http://www.{d}/")
# Hard API limit on threatEntries in one request.
MAX_ENTRIES_PER_REQUEST = 500
DOMAINS_PER_REQUEST = MAX_ENTRIES_PER_REQUEST // len(_URL_FORMS)


def urls_for(domain: str) -> list[str]:
    return [form.format(d=domain) for form in _URL_FORMS]


def domain_of(url: str) -> str:
    """The domain a matched URL belongs to, however Google canonicalised it."""
    host = urlsplit(url if "//" in url else f"//{url}").hostname or url
    return host[4:] if host.startswith("www.") else host


class GoogleSafeBrowsingChecker:
    """Checks domains against the Google Safe Browsing v4 lists."""

    name = "google_safe_browsing"

    def __init__(self, api_key: str, *, timeout: float = 30.0) -> None:
        self._api_key = api_key
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    def _payload(self, domains: list[str]) -> dict:
        entries = [{"url": u} for d in domains for u in urls_for(d)]
        return {
            "client": {"clientId": "domain-scanner-bot", "clientVersion": "0.1.0"},
            "threatInfo": {
                "threatTypes": _THREAT_TYPES,
                "platformTypes": ["ANY_PLATFORM"],
                "threatEntryTypes": ["URL"],
                "threatEntries": entries,
            },
        }

    async def _find(self, session: aiohttp.ClientSession, domains: list[str]) -> list[dict]:
        async with session.post(
            _ENDPOINT, params={"key": self._api_key}, json=self._payload(domains)
        ) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise _GsbError(f"HTTP {resp.status}: {text[:300]}")
            data = await resp.json()
        return data.get("matches") or []

    def _outcomes(
        self, domains: list[str], matches: list[dict]
    ) -> dict[str, CheckOutcome]:
        """Every domain in the batch gets a verdict; matches name the bad ones."""
        threats: dict[str, set[str]] = {}
        raw: dict[str, list[dict]] = {}
        known = set(domains)
        for match in matches:
            url = (match.get("threat") or {}).get("url") or ""
            domain = domain_of(url)
            if domain not in known:
                # A listing we cannot attribute is worse than useless — it would
                # silently belong to no domain, so say it out loud.
                log.warning("gsb.unattributed_match", url=url)
                continue
            threats.setdefault(domain, set()).add(match.get("threatType", "UNKNOWN"))
            raw.setdefault(domain, []).append(match)

        outcomes = {}
        for domain in domains:
            found = threats.get(domain)
            outcomes[domain] = (
                CheckOutcome(
                    checker=self.name,
                    verdict=Verdict.FLAGGED,
                    summary=f"GSB: {', '.join(sorted(found))}",
                    raw={"matches": raw[domain]},
                )
                if found
                else CheckOutcome(
                    checker=self.name, verdict=Verdict.CLEAN, summary="нет совпадений в GSB"
                )
            )
        return outcomes

    async def check_many(self, domains: list[str]) -> dict[str, CheckOutcome]:
        """One request per 125 domains; a failed chunk only costs its own domains."""
        outcomes: dict[str, CheckOutcome] = {}
        unique = list(dict.fromkeys(domains))
        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            for start in range(0, len(unique), DOMAINS_PER_REQUEST):
                chunk = unique[start : start + DOMAINS_PER_REQUEST]
                try:
                    matches = await self._find(session, chunk)
                except (_GsbError, aiohttp.ClientError, TimeoutError) as exc:
                    message = str(exc) if isinstance(exc, _GsbError) else f"request failed: {exc}"
                    log.warning("gsb.batch_failed", domains=len(chunk), error=message)
                    outcomes.update(
                        {d: CheckOutcome.failure(self.name, message) for d in chunk}
                    )
                    continue
                outcomes.update(self._outcomes(chunk, matches))
        return outcomes

    async def check(self, domain: str) -> CheckOutcome:
        outcomes = await self.check_many([domain])
        return outcomes.get(domain) or CheckOutcome.failure(self.name, "нет ответа от GSB")


class _GsbError(Exception):
    """A non-200 answer from the API, carried as the outcome's error text."""
