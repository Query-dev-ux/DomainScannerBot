from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from domain_scanner.db.models import DomainSource


@dataclass(slots=True)
class SourceDomain:
    """A domain as reported by an external platform, normalised for sync.

    Every domain a source reports gets checked; its platform status is kept for
    reference only and does not decide anything.
    """

    name: str
    status: str | None = None
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
    """Collapse repeats of the same domain name (e.g. one domain used by two PWAs);
    the first occurrence wins."""
    by_name: dict[str, SourceDomain] = {}
    for item in domains:
        by_name.setdefault(item.name, item)
    return list(by_name.values())
