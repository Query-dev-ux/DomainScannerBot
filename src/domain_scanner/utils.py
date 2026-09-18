from __future__ import annotations

import re

_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(\.[a-z0-9-]{1,63})+$")


def normalize_domain(raw: str | None) -> str | None:
    """Reduce a hostname or URL to a bare lowercase domain; None if it isn't one."""
    if not raw:
        return None
    value = raw.strip().lower()
    value = re.sub(r"^[a-z][a-z0-9+.-]*://", "", value)
    value = value.split("/")[0].split("?")[0].split("#")[0]
    value = value.rsplit("@", 1)[-1].split(":")[0].rstrip(".")
    if value.startswith("www."):
        value = value[4:]
    return value if _DOMAIN_RE.match(value) else None
