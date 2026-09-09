from __future__ import annotations

import asyncio
import ipaddress
import socket

from domain_scanner.checkers.base import CheckOutcome
from domain_scanner.db.models import Verdict

# Domain-based blocklists (queried as <domain>.<zone>).
# NOTE: SURBL/URIBL block queries coming from large public resolvers (e.g. 8.8.8.8).
# For reliable results the container should use its own recursive resolver.
_URIBL_ZONES = {
    "multi.surbl.org": "SURBL",
    "dbl.spamhaus.org": "Spamhaus DBL",
}

_LOOPBACK_NET = ipaddress.ip_network("127.0.0.0/8")


def _is_listing_response(addrs: list[str]) -> bool:
    """A genuine DNSBL 'listed' answer is always inside 127.0.0.0/8, but
    127.255.255.x is reserved for 'query refused / resolver blocked'."""
    parsed = []
    for a in addrs:
        try:
            parsed.append(ipaddress.ip_address(a))
        except ValueError:
            return False
    return bool(parsed) and all(
        ip in _LOOPBACK_NET and not str(ip).startswith("127.255.255.") for ip in parsed
    )


class DnsRblChecker:
    """Resolves the domain and checks it against domain blocklists (no API key)."""

    name = "dns_rbl"

    def __init__(self, *, timeout: float = 5.0) -> None:
        self._timeout = timeout

    async def _resolve(self, host: str) -> list[str]:
        loop = asyncio.get_running_loop()
        try:
            infos = await asyncio.wait_for(
                loop.getaddrinfo(host, None, type=socket.SOCK_STREAM),
                timeout=self._timeout,
            )
        except (TimeoutError, socket.gaierror, OSError):
            return []
        return sorted({info[4][0] for info in infos})

    async def check(self, domain: str) -> CheckOutcome:
        if not await self._resolve(domain):
            return CheckOutcome(
                checker=self.name,
                verdict=Verdict.SUSPICIOUS,
                summary="домен не резолвится (NXDOMAIN / нет A-записи)",
            )

        results = await asyncio.gather(
            *(self._resolve(f"{domain}.{zone}") for zone in _URIBL_ZONES)
        )
        listed = {
            label: ", ".join(addrs)
            for (zone, label), addrs in zip(_URIBL_ZONES.items(), results, strict=True)
            if _is_listing_response(addrs)
        }
        if listed:
            detail = "; ".join(f"{k} ({v})" for k, v in listed.items())
            return CheckOutcome(
                checker=self.name,
                verdict=Verdict.FLAGGED,
                summary=f"в блоклистах: {detail}",
                raw={"listed": listed},
            )
        return CheckOutcome(
            checker=self.name, verdict=Verdict.CLEAN, summary="не найден в блоклистах"
        )
