from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aiohttp
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from domain_scanner.logging import get_logger

log = get_logger(__name__)

# Domain.status codes from the PWA.partners Open API (models.Domain.status):
# 0 - создаётся, 1 - активен, 2 - ошибка создания, 5 - требуется настройка ns1 и ns2,
# 6 - в процессе выпуска сертификата, 7 - домен занят, не свободен, 8 - удаляется,
# 9 - удалён, 10 - настройка серверов имён, 11 - не удалось выпустить
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


@dataclass(slots=True)
class PwaDomain:
    uuid: str
    domain: str
    status: int | None
    pwa_uuid: str | None
    raw: dict[str, Any]

    @property
    def is_active(self) -> bool:
        return self.status == PWA_STATUS_ACTIVE

    @property
    def status_label(self) -> str:
        if self.status is None:
            return "неизвестно"
        return PWA_STATUS_LABELS.get(self.status, f"код {self.status}")


class PwaPartnersError(RuntimeError):
    pass


class PwaPartnersClient:
    """Async client for the PWA.partners Open API (dash_api)."""

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
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> PwaPartnersClient:
        self._session = aiohttp.ClientSession(headers=self._headers, timeout=self._timeout)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _require_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise RuntimeError("Client must be used as an async context manager")
        return self._session

    @retry(
        retry=retry_if_exception_type(aiohttp.ClientError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        session = self._require_session()
        url = f"{self._base_url}{path}"
        async with session.get(url, params=params) as resp:
            body = await resp.text()
            if resp.status >= 400:
                raise PwaPartnersError(f"GET {path} -> {resp.status}: {body[:400]}")
            return await resp.json()

    async def validate(self) -> dict[str, Any]:
        return await self._get("/dash_api/auth/validate")

    async def iter_domains(self) -> list[PwaDomain]:
        """Fetch the full paginated domain list."""
        page = 1
        collected: list[PwaDomain] = []
        while True:
            data = await self._get(
                "/dash_api/domains/list",
                params={"page": page, "page_size": self._page_size},
            )
            items = data.get("domains") or []
            for item in items:
                name = (item.get("domain") or "").strip().lower()
                if not name:
                    continue
                collected.append(
                    PwaDomain(
                        uuid=item.get("uuid", ""),
                        domain=name,
                        status=item.get("status"),
                        pwa_uuid=item.get("pwa_uuid"),
                        raw=item,
                    )
                )
            total = int(data.get("total") or 0)
            if page * self._page_size >= total or not items:
                break
            page += 1
        log.info("pwa.domains.fetched", count=len(collected))
        return collected
