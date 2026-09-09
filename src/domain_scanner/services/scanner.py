from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

from domain_scanner.checkers.base import Checker, CheckOutcome
from domain_scanner.db import session_scope
from domain_scanner.db.models import Domain, Scan, ScanCheck, Verdict
from domain_scanner.logging import get_logger
from domain_scanner.repositories import DomainRepository

log = get_logger(__name__)


def aggregate_verdict(outcomes: list[CheckOutcome]) -> Verdict:
    real = [o.verdict for o in outcomes if o.verdict is not Verdict.ERROR]
    if not real:
        return Verdict.ERROR
    return max(real, key=lambda v: v.severity)


@dataclass(slots=True)
class ScanReport:
    domain: str
    verdict: Verdict
    previous_verdict: Verdict
    changed: bool
    outcomes: list[CheckOutcome] = field(default_factory=list)

    @property
    def worsened(self) -> bool:
        return self.verdict.severity > self.previous_verdict.severity

    @property
    def needs_alert(self) -> bool:
        return self.changed and self.verdict in (Verdict.SUSPICIOUS, Verdict.FLAGGED)


class ScannerService:
    def __init__(self, checkers: list[Checker], *, concurrency: int = 5) -> None:
        self.checkers = checkers
        self._semaphore = asyncio.Semaphore(concurrency)

    async def _run_checkers(self, domain: str) -> list[CheckOutcome]:
        async def _one(checker: Checker) -> CheckOutcome:
            try:
                return await checker.check(domain)
            except Exception as exc:
                log.exception("checker.crashed", checker=checker.name, domain=domain)
                return CheckOutcome.failure(checker.name, f"{type(exc).__name__}: {exc}")

        return await asyncio.gather(*(_one(c) for c in self.checkers))

    async def scan_domain(self, domain_id: int) -> ScanReport | None:
        async with self._semaphore:
            return await self._scan_domain(domain_id)

    async def _scan_domain(self, domain_id: int) -> ScanReport | None:
        async with session_scope() as session:
            domain = await session.get(Domain, domain_id)
            if domain is None:
                return None
            name = domain.name
            previous = domain.current_verdict
            started = datetime.now(UTC)

        outcomes = await self._run_checkers(name)
        verdict = aggregate_verdict(outcomes)
        finished = datetime.now(UTC)

        async with session_scope() as session:
            domain = await session.get(Domain, domain_id)
            if domain is None:
                return None
            changed = verdict != domain.current_verdict
            scan = Scan(
                domain_id=domain_id,
                verdict=verdict,
                previous_verdict=domain.current_verdict,
                changed=changed,
                started_at=started,
                finished_at=finished,
                checks=[
                    ScanCheck(
                        checker=o.checker,
                        verdict=o.verdict,
                        summary=o.summary,
                        error=o.error,
                        raw=o.raw or None,
                        created_at=finished,
                    )
                    for o in outcomes
                ],
            )
            session.add(scan)
            domain.current_verdict = verdict
            domain.last_scanned_at = finished

        report = ScanReport(
            domain=name,
            verdict=verdict,
            previous_verdict=previous,
            changed=verdict != previous,
            outcomes=outcomes,
        )
        log.info(
            "scan.done",
            domain=name,
            verdict=verdict.value,
            previous=previous.value,
            changed=report.changed,
        )
        return report

    async def scan_many(self, domain_ids: list[int]) -> list[ScanReport]:
        results = await asyncio.gather(*(self.scan_domain(i) for i in domain_ids))
        return [r for r in results if r is not None]


async def collect_due_domain_ids(older_than_minutes: int) -> list[int]:
    from datetime import timedelta

    async with session_scope() as session:
        repo = DomainRepository(session)
        domains = await repo.list_due_for_scan(timedelta(minutes=older_than_minutes))
        return [d.id for d in domains]
