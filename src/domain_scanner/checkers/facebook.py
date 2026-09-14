from __future__ import annotations

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
        if status == 200 and "id" in payload:
            og = payload.get("og_object") or {}
            shares = (payload.get("engagement") or {}).get("share_count")
            bits = []
            if og.get("title"):
                bits.append(f"og:title «{og['title']}»")
            if shares is not None:
                bits.append(f"шеров: {shares}")
            detail = "; ".join(bits) if bits else "страница прочитана"
            return CheckOutcome(
                checker=NAME,
                verdict=Verdict.CLEAN,
                summary=f"FB отдаёт ссылку нормально ({detail})",
                raw=payload,
            )
        return CheckOutcome(
            checker=NAME,
            verdict=Verdict.ERROR,
            error=f"неожиданный ответ Graph API (HTTP {status})",
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

    Uses the Graph API URL node — the same lookup that powers the Sharing
    Debugger — with an app access token (`{app_id}|{app_secret}`), which needs no
    user login. A domain banned under Community Standards comes back as an error
    instead of a scraped Open Graph object.
    """

    name = NAME

    def __init__(
        self, app_id: str, app_secret: str, *, timeout: float = 25.0
    ) -> None:
        self._token = f"{app_id}|{app_secret}"
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def check(self, domain: str) -> CheckOutcome:
        params = {
            "id": f"https://{domain}/",
            "fields": "og_object,engagement",
            "access_token": self._token,
        }
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.get(_ENDPOINT, params=params) as resp:
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
