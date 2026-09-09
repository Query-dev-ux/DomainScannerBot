from __future__ import annotations

import aiohttp

from domain_scanner.checkers.base import CheckOutcome
from domain_scanner.db.models import Verdict

_ENDPOINT = "https://www.virustotal.com/api/v3/domains/{domain}"


class VirusTotalChecker:
    """Checks a domain's aggregated reputation via VirusTotal v3.

    Free tier: 4 requests/min, 500/day. The scanner throttles via SCAN_CONCURRENCY,
    but for large domain sets consider a paid key or a longer scan interval.
    """

    name = "virustotal"

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 20.0,
        suspicious_threshold: int = 1,
        flagged_threshold: int = 3,
    ) -> None:
        self._api_key = api_key
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._suspicious_threshold = suspicious_threshold
        self._flagged_threshold = flagged_threshold

    async def check(self, domain: str) -> CheckOutcome:
        headers = {"x-apikey": self._api_key}
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.get(
                    _ENDPOINT.format(domain=domain), headers=headers
                ) as resp:
                    text = await resp.text()
                    if resp.status == 404:
                        return CheckOutcome(
                            checker=self.name,
                            verdict=Verdict.UNKNOWN,
                            summary="домен не найден в VirusTotal",
                        )
                    if resp.status == 429:
                        return CheckOutcome.failure(self.name, "rate limited (429)")
                    if resp.status != 200:
                        return CheckOutcome.failure(
                            self.name, f"HTTP {resp.status}: {text[:300]}"
                        )
                    data = await resp.json()
        except aiohttp.ClientError as exc:
            return CheckOutcome.failure(self.name, f"request failed: {exc}")

        attrs = data.get("data", {}).get("attributes", {})
        stats = attrs.get("last_analysis_stats", {})
        malicious = int(stats.get("malicious", 0))
        suspicious = int(stats.get("suspicious", 0))
        hits = malicious + suspicious

        if malicious >= self._flagged_threshold:
            verdict = Verdict.FLAGGED
        elif hits >= self._suspicious_threshold:
            verdict = Verdict.SUSPICIOUS
        else:
            verdict = Verdict.CLEAN

        return CheckOutcome(
            checker=self.name,
            verdict=verdict,
            summary=f"VT: malicious={malicious}, suspicious={suspicious}",
            raw={"last_analysis_stats": stats, "reputation": attrs.get("reputation")},
        )
