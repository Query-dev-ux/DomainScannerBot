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
