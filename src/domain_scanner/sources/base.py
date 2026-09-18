from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from domain_scanner.db.models import DomainSource


@dataclass(slots=True)
class SourceDomain:
    """A domain as reported by an external platform, normalised for sync."""

    name: str
    is_active: bool
    status: str | None = None
    status_label: str | None = None
    external_id: str | None = None
    external_parent_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class SourceError(RuntimeError):
    """The platform API answered with an error or garbage."""


@runtime_checkable
class DomainProvider(Protocol):
    source: DomainSource
    title: str

    async def fetch_domains(self) -> list[SourceDomain]: ...


def dedupe(domains: list[SourceDomain]) -> list[SourceDomain]:
    """Collapse repeats of the same domain name (e.g. one domain used by two PWAs).

    The first occurrence wins, except that a domain counts as active if any of its
    occurrences is active — being live anywhere means traffic can hit it.
    """
    by_name: dict[str, SourceDomain] = {}
    for item in domains:
        seen = by_name.get(item.name)
        if seen is None:
            by_name[item.name] = item
        elif item.is_active and not seen.is_active:
            by_name[item.name] = item
    return list(by_name.values())
