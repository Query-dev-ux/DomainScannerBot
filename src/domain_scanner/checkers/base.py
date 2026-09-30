from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from domain_scanner.db.models import Verdict


@dataclass(slots=True)
class CheckOutcome:
    checker: str
    verdict: Verdict
    summary: str | None = None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def failure(cls, checker: str, message: str) -> CheckOutcome:
        return cls(checker=checker, verdict=Verdict.ERROR, error=message)


@runtime_checkable
class Checker(Protocol):
    name: str

    async def check(self, domain: str) -> CheckOutcome: ...


@runtime_checkable
class BatchChecker(Protocol):
    """A checker whose API judges many domains in one request.

    A scan of the whole list calls `check_many` once instead of once per domain,
    which is the difference between staying inside a daily API quota and running
    out of it (see GoogleSafeBrowsingChecker). Every batch checker also answers
    `check` for a single domain, for /check and the recheck button.
    """

    name: str

    async def check(self, domain: str) -> CheckOutcome: ...

    async def check_many(self, domains: list[str]) -> dict[str, CheckOutcome]: ...
