from __future__ import annotations

from typing import Any

import aiohttp
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from domain_scanner.db.models import DomainSource
from domain_scanner.logging import get_logger
from domain_scanner.sources.base import SourceDomain, SourceError
from domain_scanner.utils import normalize_domain

log = get_logger(__name__)

# Domain.status codes from the PWA.partners Open API (models.Domain.status).
PWA_STATUS_ACTIVE = 1
PWA_STATUS_LABELS: dict[int, str] = {
    0: "создаётся",
    1: "активен",
    2: "ошибка создания",
    5: "требуется настройка NS",
    6: "выпуск сертификата",
    7: "домен занят",
    8: "удаляется",
    9: "удалён",
    10: "настройка NS",
    11: "ошибка выпуска сертификата",
}


def parse_domains(items: list[dict[str, Any]]) -> list[SourceDomain]:
    """Map one page of `GET /dash_api/domains/list` onto SourceDomain."""
    result: list[SourceDomain] = []
    for item in items:
        name = normalize_domain(item.get("domain"))
        if name is None:
            continue
        status = item.get("status")
        result.append(
            SourceDomain(
                name=name,
                is_active=status == PWA_STATUS_ACTIVE,
                status=None if status is None else str(status),
                status_label=(
                    PWA_STATUS_LABELS.get(status, f"код {status}")
                    if status is not None
                    else None
                ),
                external_id=item.get("uuid") or None,
                external_parent_id=item.get("pwa_uuid") or None,
                raw=item,
            )
        )
    return result


class PwaPartnersProvider:
    """PWA.partners Open API (dash_api). Auth via X-Api-Key / X-Team-UUID headers."""

    source = DomainSource.PWA
    title = "PWA.partners"

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

    async def fetch_domains(self) -> list[SourceDomain]:
        collected: list[SourceDomain] = []
        async with aiohttp.ClientSession(
            headers=self._headers, timeout=self._timeout
        ) as session:
            page = 1
            while True:
                data = await self._get(
                    session,
                    "/dash_api/domains/list",
                    {"page": page, "page_size": self._page_size},
                )
                items = data.get("domains") or []
                collected.extend(parse_domains(items))
                total = int(data.get("total") or 0)
                if not items or page * self._page_size >= total:
                    break
                page += 1
        log.info("source.fetched", source=self.source.value, count=len(collected))
        return collected
