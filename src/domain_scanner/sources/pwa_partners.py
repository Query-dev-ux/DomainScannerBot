from __future__ import annotations

from typing import Any

import aiohttp
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from domain_scanner.db.models import DomainSource
from domain_scanner.logging import get_logger
from domain_scanner.sources.base import SourceDomain, SourceError
from domain_scanner.utils import normalize_domain

log = get_logger(__name__)

def parse_teamates(teamates: list[dict[str, Any]]) -> dict[str, str]:
    """uuid -> display name, from `GET /dash_api/team/list`."""
    names = {}
    for teamate in teamates:
        uuid = teamate.get("uuid")
        name = teamate.get("team_username") or teamate.get("login")
        if uuid and name:
            names[uuid] = name
    return names


def parse_domains(
    items: list[dict[str, Any]], owners: dict[str, str] | None = None
) -> list[SourceDomain]:
    """Map one page of `GET /dash_api/domains/list` onto SourceDomain.

    The domain only carries the teamate's uuid, so `owners` maps those onto the
    names the dashboard shows.
    """
    owners = owners or {}
    result: list[SourceDomain] = []
    for item in items:
        name = normalize_domain(item.get("domain"))
        if name is None:
            continue
        status = item.get("status")
        result.append(
            SourceDomain(
                name=name,
                status=None if status is None else str(status),
                owner=owners.get(item.get("teamate_uuid") or ""),
                external_id=item.get("uuid") or None,
                external_parent_id=item.get("pwa_uuid") or None,
                raw=item,
            )
        )
    return result


class PwaPartnersProvider:
    """PWApartners Open API (dash_api). Auth via X-Api-Key / X-Team-UUID headers."""

    source = DomainSource.PWA
    title = "PWApartners"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        team_uuid: str,
        teamate_uuid: str | None = None,
        *,
        timeout: float = 30.0,
        page_size: int = 200,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._page_size = page_size
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._headers = {
            "X-Api-Key": api_key,
            "X-Team-UUID": team_uuid,
            "Accept": "application/json",
        }
        if teamate_uuid:
            self._headers["X-Teamate-UUID"] = teamate_uuid

    @retry(
        retry=retry_if_exception_type(aiohttp.ClientError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _get(
        self, session: aiohttp.ClientSession, path: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        async with session.get(f"{self._base_url}{path}", params=params) as resp:
            body = await resp.text()
            if resp.status >= 400:
                raise SourceError(f"GET {path} → HTTP {resp.status}: {body[:300]}")
            data = await resp.json(content_type=None)
        if not isinstance(data, dict):
            raise SourceError(f"GET {path}: ожидался JSON-объект")
        return data

    async def _fetch_owners(self, session: aiohttp.ClientSession) -> dict[str, str]:
        """Team members, so a domain's teamate uuid can be shown as a name.

        A missing or broken team list must not cost us the domains, so failures
        here only mean the domains arrive without an owner.
        """
        owners: dict[str, str] = {}
        page = 1
        try:
            while True:
                data = await self._get(
                    session, "/dash_api/team/list", {"page": page, "page_size": 200}
                )
                teamates = data.get("teamates") or []
                owners.update(parse_teamates(teamates))
                total = int(data.get("total") or 0)
                if not teamates or page * 200 >= total:
                    break
                page += 1
        except Exception as exc:
            log.warning("pwa.teamates.failed", error=f"{type(exc).__name__}: {exc}")
        return owners

    async def fetch_domains(self) -> list[SourceDomain]:
        collected: list[SourceDomain] = []
        async with aiohttp.ClientSession(
            headers=self._headers, timeout=self._timeout
        ) as session:
            owners = await self._fetch_owners(session)
            page = 1
            while True:
                data = await self._get(
                    session,
                    "/dash_api/domains/list",
                    {"page": page, "page_size": self._page_size},
                )
                items = data.get("domains") or []
                collected.extend(parse_domains(items, owners))
                total = int(data.get("total") or 0)
                if not items or page * self._page_size >= total:
                    break
                page += 1
        log.info("source.fetched", source=self.source.value, count=len(collected))
        return collected
