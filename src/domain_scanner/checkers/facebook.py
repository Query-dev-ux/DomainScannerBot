from __future__ import annotations

import time
from collections import deque
from typing import Any

import aiohttp

from domain_scanner.checkers.base import CheckOutcome
from domain_scanner.db.models import Verdict
from domain_scanner.logging import get_logger

log = get_logger(__name__)

NAME = "facebook"

_ENDPOINT = "https://graph.facebook.com/v21.0/"

# Graph API error codes that mean "ask again later", not "domain is bad".
_TRANSIENT_CODES = frozenset({1, 2, 4, 17, 32, 341, 368, 613})

# Bad/expired app credentials — a config problem, worth saying so plainly.
_AUTH_CODES = frozenset({102, 190, 200, 458, 467})

# Facebook asking us to slow down, and Facebook having cut the app off.
_RATE_LIMIT_CODES = frozenset({4, 17, 32, 613})
_BLOCKED_CODES = frozenset({200, 368})
_RATE_LIMIT_PAUSE = 30 * 60
_BLOCKED_PAUSE = 6 * 60 * 60

# Substrings that indicate Facebook refuses the URL on policy grounds.
# Matched case-insensitively against the error message + user_title + user_msg.
_BLOCKED_MARKERS = (
    "community standards",
    "not allowed",
    "is blocked",
    "has been blocked",
    "abusive",
    "malicious",
    "spam",
    "unsafe",
    "circumvent",
)

# Substrings that mean FB's crawler could not read the page. Not a ban, but for a
# landing page it still means the link will render badly (or the cloak misfired).
_UNFETCHABLE_MARKERS = (
    "no data was scraped",
    "error parsing input url",
    "could not fetch",
    "bad response code",
    "could not resolve",
    "timed out",
)


def _error_text(error: dict[str, Any]) -> str:
    parts = [
        str(error.get(key, ""))
        for key in ("message", "error_user_title", "error_user_msg", "type")
    ]
    return " ".join(parts).lower()


def classify(status: int, payload: dict[str, Any]) -> CheckOutcome:
    """Map a Graph API URL-node response onto a verdict.

    Pure function — no network — so the classification rules stay unit-testable.
    The full payload is always kept in `raw` so unfamiliar responses can be
    inspected later and the marker lists tightened against real data.
    """
    error = payload.get("error")

    if not error:
        if status != 200:
            return CheckOutcome(
                checker=NAME,
                verdict=Verdict.ERROR,
                error=f"неожиданный ответ Graph API (HTTP {status})",
                raw=payload,
            )
        # A scrape that reached the page comes back with what FB read off it.
        # A domain FB cannot open returns only url/type/updated_time — that is
        # how a dead or blocked domain looks, verified on a domain that no
        # longer resolves.
        title = payload.get("title") or (payload.get("og_object") or {}).get("title")
        if title or payload.get("description") or payload.get("image"):
            detail = f": «{title}»" if title else ""
            return CheckOutcome(
                checker=NAME,
                verdict=Verdict.CLEAN,
                summary=f"FB читает страницу{detail}",
                raw=payload,
            )
        if "url" in payload or "id" in payload:
            return CheckOutcome(
                checker=NAME,
                verdict=Verdict.SUSPICIOUS,
                summary="FB не смог прочитать страницу",
                raw=payload,
            )
        return CheckOutcome(
            checker=NAME,
            verdict=Verdict.ERROR,
            error="неожиданный ответ Graph API: ни страницы, ни ошибки",
            raw=payload,
        )

    code = error.get("code")
    text = _error_text(error)

    if error.get("is_transient") or code in _TRANSIENT_CODES:
        return CheckOutcome(
            checker=NAME,
            verdict=Verdict.ERROR,
            error=f"временная ошибка Graph API (code={code}): {error.get('message', '')}",
            raw=payload,
        )

    if code in _AUTH_CODES:
        return CheckOutcome(
            checker=NAME,
            verdict=Verdict.ERROR,
            error=(
                f"Graph API не принял креды (code={code}): {error.get('message', '')} "
                "— проверьте FB_APP_ID / FB_APP_SECRET в .env"
            ),
            raw=payload,
        )

    if any(marker in text for marker in _BLOCKED_MARKERS):
        return CheckOutcome(
            checker=NAME,
            verdict=Verdict.FLAGGED,
            summary=f"FB блокирует ссылку: {error.get('message', '')}",
            raw=payload,
        )

    if any(marker in text for marker in _UNFETCHABLE_MARKERS):
        return CheckOutcome(
            checker=NAME,
            verdict=Verdict.SUSPICIOUS,
            summary=f"краулер FB не смог прочитать страницу: {error.get('message', '')}",
            raw=payload,
        )

    # Unknown error shape: stay quiet (ERROR is ignored when other checkers
    # succeeded) but log it so the marker lists can be extended.
    log.warning("facebook.unknown_error", code=code, message=error.get("message"))
    return CheckOutcome(
        checker=NAME,
        verdict=Verdict.ERROR,
        error=f"неизвестная ошибка Graph API (code={code}): {error.get('message', '')}",
        raw=payload,
    )


class FacebookUrlChecker:
    """Checks whether Facebook will accept a link to the domain.

    Uses the Graph API URL node with `scrape=true` — the same call the Sharing
    Debugger makes — authenticated by an app access token (`{app_id}|{app_secret}`),
    which needs no user login.

    `scrape=true` matters: without it Graph only replays what Facebook already has
    cached and answers with zero engagement for anything it has never seen, so a
    dead or blocked domain looks exactly like a healthy one. With it, Facebook
    actually fetches the page and the answer says whether it could.
    """

    name = NAME

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        timeout: float = 40.0,
        hourly_limit: int = 60,
    ) -> None:
        self._token = f"{app_id}|{app_secret}"
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._hourly_limit = hourly_limit
        self._recent: deque[float] = deque()
        self._paused_until = 0.0

    def _budget_left(self, now: float) -> bool:
        while self._recent and now - self._recent[0] > 3600:
            self._recent.popleft()
        return len(self._recent) < self._hourly_limit

    def _pause(self, seconds: float, why: str) -> None:
        self._paused_until = max(self._paused_until, time.monotonic() + seconds)
        log.warning("facebook.paused", seconds=int(seconds), why=why)

    async def check(self, domain: str) -> CheckOutcome:
        # Every call makes Facebook fetch the page, so it is easy to run into the
        # app's request limit — and Meta answers a sustained overrun by blocking
        # the app's API access outright. Stay inside an hourly budget, and back
        # off hard once Facebook complains.
        now = time.monotonic()
        if now < self._paused_until:
            left = int(self._paused_until - now)
            return CheckOutcome.failure(NAME, f"пропущено: пауза после лимита ({left} с)")
        if not self._budget_left(now):
            return CheckOutcome.failure(NAME, "пропущено: исчерпан лимит запросов за час")
        self._recent.append(now)

        outcome = await self._request(domain)
        code = (outcome.raw.get("error") or {}).get("code") if outcome.raw else None
        if code in _RATE_LIMIT_CODES:
            self._pause(_RATE_LIMIT_PAUSE, f"code={code}")
        elif code in _BLOCKED_CODES:
            self._pause(_BLOCKED_PAUSE, f"code={code}")
        return outcome

    async def _request(self, domain: str) -> CheckOutcome:
        data = {
            "id": f"https://{domain}/",
            "scrape": "true",
            "access_token": self._token,
        }
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.post(_ENDPOINT, data=data) as resp:
                    status = resp.status
                    try:
                        payload = await resp.json(content_type=None)
                    except (ValueError, aiohttp.ClientError):
                        body = await resp.text()
                        return CheckOutcome.failure(
                            NAME, f"HTTP {status}, не JSON: {body[:300]}"
                        )
        except aiohttp.ClientError as exc:
            return CheckOutcome.failure(NAME, f"request failed: {exc}")
        except TimeoutError:
            return CheckOutcome.failure(NAME, "timeout")

        if not isinstance(payload, dict):
            return CheckOutcome.failure(NAME, f"HTTP {status}, неожиданный JSON")

        return classify(status, payload)
