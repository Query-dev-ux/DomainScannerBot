from __future__ import annotations

import base64
from typing import Any

import aiohttp
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from domain_scanner.db.models import DomainSource
from domain_scanner.logging import get_logger
from domain_scanner.sources.base import SourceDomain, SourceError
from domain_scanner.utils import normalize_domain

log = get_logger(__name__)


def basic_auth_header(login: str, password: str) -> str:
    # Built by hand: aiohttp.BasicAuth is deprecated and goes away in aiohttp 4.
    token = base64.b64encode(f"{login}:{password}".encode()).decode("ascii")
    return f"Basic {token}"


# Statuses stored on the domain, read back by the source_status checker.
STATUS_BANNED = "banned"
STATUS_DISABLED = "disabled"
STATUS_OK = "ok"


def domain_status(entry: dict[str, Any]) -> str:
    """What `domains[]` says about one domain.

    `is_baned_register` is the flag behind the "Domain is banned" the dashboard
    shows; a banned domain also comes with is_disable and a cleared cloudflare_id.
    """
    if entry.get("is_baned_register"):
        return STATUS_BANNED
    if entry.get("is_disable"):
        return STATUS_DISABLED
    return STATUS_OK


def _from_domains_field(pwa: dict[str, Any]) -> list[SourceDomain]:
    result = []
    for entry in pwa.get("domains") or []:
        if not isinstance(entry, dict):
            continue
        name = normalize_domain(entry.get("domain"))
        if name is None:
            continue
        result.append(
            SourceDomain(
                name=name,
                status=domain_status(entry),
                owner=pwa.get("createdUsername") or None,
                external_id=entry.get("cid"),
                external_parent_id=pwa.get("id"),
                raw={
                    "pwa_id": pwa.get("id"),
                    "pwa_name": pwa.get("name"),
                    "is_main": entry.get("is_main"),
                    "expiry": entry.get("expiryDatetime"),
                    "is_baned_register": entry.get("is_baned_register"),
                    "is_disable": entry.get("is_disable"),
                },
            )
        )
    return result


def _split_domains(pwa: dict[str, Any]) -> list[SourceDomain]:
    return [
        SourceDomain(
            name=name,
            owner=pwa.get("createdUsername") or None,
            external_id=split.get("id"),
            external_parent_id=pwa.get("id"),
            raw={"pwa_id": pwa.get("id"), "pwa_name": pwa.get("name"), "role": "split"},
        )
        for split in pwa.get("splits") or []
        if isinstance(split, dict) and (name := normalize_domain(split.get("domain")))
    ]


def _legacy_domains(pwa: dict[str, Any]) -> list[SourceDomain]:
    """Before `domains[]` existed the API gave only bare hostnames."""
    plain = [pwa.get("domain"), *(d for d in pwa.get("extDomains") or [] if isinstance(d, str))]
    return [
        SourceDomain(
            name=name,
            owner=pwa.get("createdUsername") or None,
            external_parent_id=pwa.get("id"),
            raw={"pwa_id": pwa.get("id"), "pwa_name": pwa.get("name")},
        )
        for raw in plain
        if (name := normalize_domain(raw))
    ]


def parse_pwas(pwas: list[dict[str, Any]]) -> list[SourceDomain]:
    """Map one page of `POST /pwa/list` onto SourceDomain.

    `domains[]` is the detailed list — main and extra domains with their own id,
    expiry and ban flag — and is preferred. Split domains live in their own field
    and are added on top; if the platform ever stops sending `domains[]`, the bare
    `domain`/`extDomains` strings are used instead.
    """
    result: list[SourceDomain] = []
    for pwa in pwas:
        detailed = _from_domains_field(pwa)
        result += detailed or _legacy_domains(pwa)
        result += _split_domains(pwa)
    return result


class SkakAppProvider:
    """SkakApp API — https://uclient.skakapp.com/api-docs/.

    HTTP Basic auth with the account login and password — verified against the live
    API; the separate API key SkakApp issues is rejected here and is not needed.
    """

    source = DomainSource.SKAKAPP
    title = "SkakApp"

    def __init__(
        self,
        base_url: str,
        login: str,
        password: str,
        *,
        timeout: float = 30.0,
        page_size: int = 500,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": basic_auth_header(login, password),
            "Accept": "application/json",
        }
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._page_size = page_size

    @retry(
        retry=retry_if_exception_type(aiohttp.ClientError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _post(
        self, session: aiohttp.ClientSession, path: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        async with session.post(f"{self._base_url}{path}", json=body) as resp:
            text = await resp.text()
            if resp.status == 401:
                raise SourceError(
                    "HTTP 401: SkakApp не принял логин/пароль — проверьте "
                    "SKAKAPP_LOGIN и SKAKAPP_PASSWORD"
                )
            if resp.status >= 400:
                raise SourceError(f"POST {path} → HTTP {resp.status}: {text[:300]}")
            data = await resp.json(content_type=None)
        if not isinstance(data, dict):
            raise SourceError(f"POST {path}: ожидался JSON-объект")
        return data

    async def fetch_domains(self) -> list[SourceDomain]:
        collected: list[SourceDomain] = []
        async with aiohttp.ClientSession(
            timeout=self._timeout, headers=self._headers
        ) as session:
            page = 1
            while True:
                data = await self._post(
                    session,
                    "/pwa/list",
                    {"page": page, "pageSize": self._page_size, "isArchive": False},
                )
                pwas = data.get("data") or []
                collected.extend(parse_pwas(pwas))
                total = int(data.get("total") or 0)
                if not pwas or page * self._page_size >= total:
                    break
                page += 1
        log.info("source.fetched", source=self.source.value, count=len(collected))
        return collected
