from __future__ import annotations

from typing import Any

import aiohttp
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from domain_scanner.db.models import DomainSource
from domain_scanner.logging import get_logger
from domain_scanner.sources.base import SourceDomain, SourceError
from domain_scanner.utils import normalize_domain

log = get_logger(__name__)

UCLIENT_STATUS_ACTIVE = "ACTIVE"
UCLIENT_STATUS_LABELS: dict[str, str] = {
    "NEW": "новая",
    "ACTIVE": "активна",
    "DISABLE": "выключена",
    "DISABLE_BALANCE": "выключена (баланс)",
    "ARCHIVE": "в архиве",
}

# How a domain is attached to its PWA — shown next to the status.
_ROLE_LABELS = {"main": "", "ext": "доп. домен", "split": "сплит"}


def _pwa_domains(pwa: dict[str, Any]) -> list[tuple[str | None, str, str | None]]:
    """(raw domain, role, split id) for every domain a PWA serves traffic on."""
    found: list[tuple[str | None, str, str | None]] = [(pwa.get("domain"), "main", None)]
    found += [(d, "ext", None) for d in pwa.get("extDomains") or [] if isinstance(d, str)]
    found += [
        (s.get("domain"), "split", s.get("id"))
        for s in pwa.get("splits") or []
        if isinstance(s, dict)
    ]
    return found


def parse_pwas(pwas: list[dict[str, Any]]) -> list[SourceDomain]:
    """Map one page of `POST /pwa/list` onto SourceDomain.

    A PWA can carry a main domain, extra domains and split domains; all of them
    receive traffic, so each becomes its own SourceDomain with the PWA's status.
    """
    result: list[SourceDomain] = []
    for pwa in pwas:
        status = pwa.get("status")
        base_label = UCLIENT_STATUS_LABELS.get(status or "", status or "неизвестно")
        for raw_domain, role, split_id in _pwa_domains(pwa):
            name = normalize_domain(raw_domain)
            if name is None:
                continue
            role_label = _ROLE_LABELS[role]
            result.append(
                SourceDomain(
                    name=name,
                    is_active=status == UCLIENT_STATUS_ACTIVE,
                    status=status,
                    status_label=f"{base_label} · {role_label}" if role_label else base_label,
                    external_id=split_id,
                    external_parent_id=pwa.get("id"),
                    raw={
                        "pwa_id": pwa.get("id"),
                        "pwa_name": pwa.get("name"),
                        "role": role,
                    },
                )
            )
    return result


class UClientProvider:
    """UClient (skakapp) API — https://uclient.skakapp.com/api-docs/.

    HTTP Basic auth. The backend is Yii2, where the API key normally goes in as the
    username with an empty password; both are configurable.
    """

    source = DomainSource.UCLIENT
    title = "UClient"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        password: str = "",
        *,
        timeout: float = 30.0,
        page_size: int = 500,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = aiohttp.BasicAuth(api_key, password)
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
                    "HTTP 401: UClient не принял ключ — проверьте UCLIENT_API_KEY "
                    "(и UCLIENT_API_PASSWORD, если он нужен)"
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
            auth=self._auth,
            timeout=self._timeout,
            headers={"Accept": "application/json"},
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
