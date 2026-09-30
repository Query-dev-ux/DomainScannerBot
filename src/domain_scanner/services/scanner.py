from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select

from domain_scanner.checkers.base import BatchChecker, Checker, CheckOutcome
from domain_scanner.checkers.source_status import (
    NOT_LIVE_SUMMARY,
    check_source_status,
    is_not_live,
)
from domain_scanner.db import session_scope
from domain_scanner.db.models import Domain, DomainSource, Scan, ScanCheck, Verdict
from domain_scanner.logging import get_logger
from domain_scanner.repositories import DomainRepository

log = get_logger(__name__)


def excuse_missing_dns(
    outcomes: list[CheckOutcome], source: DomainSource | None, status: str | None
) -> list[CheckOutcome]:
    """Do not hold "no DNS" against a domain the platform has not put in service.

    A domain being set up in PWApartners has no A-record yet, by design. Calling
    that "под подозрением" alerted the group about every domain somebody was in
    the middle of buying. The verdict becomes "не проверен" rather than "чисто":
    without DNS the blocklists were never asked either.
    """
    if not is_not_live(source, status):
        return outcomes
    return [
        CheckOutcome(
            checker=o.checker,
            verdict=Verdict.UNKNOWN,
            summary=NOT_LIVE_SUMMARY,
            raw=o.raw,
        )
        if o.checker == "dns_rbl" and (o.raw or {}).get("resolves") is False
        else o
        for o in outcomes
    ]


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
    domain_id: int | None = None
    scan_id: int | None = None
    source: DomainSource | None = None
    owner: str | None = None
    finished_at: datetime | None = None

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

    async def _run_checkers(
        self, domain: str, ready: dict[str, CheckOutcome] | None = None
    ) -> list[CheckOutcome]:
        """Run the checkers, minus the ones a batch pass has already answered."""
        ready = ready or {}
        todo = [c for c in self.checkers if c.name not in ready]

        async def _one(checker: Checker) -> CheckOutcome:
            try:
                return await checker.check(domain)
            except Exception as exc:
                log.exception("checker.crashed", checker=checker.name, domain=domain)
                return CheckOutcome.failure(checker.name, f"{type(exc).__name__}: {exc}")

        fresh = await asyncio.gather(*(_one(c) for c in todo))
        # Keep the checkers in their configured order whichever way they ran.
        by_name = {**ready, **{o.checker: o for o in fresh}}
        return [by_name[c.name] for c in self.checkers if c.name in by_name]

    async def _batch_outcomes(self, names: list[str]) -> dict[str, dict[str, CheckOutcome]]:
        """Ask every batch checker about the whole list, once.

        Safe Browsing allows 125 domains per request, so a full scan costs a few
        requests instead of one per domain. A checker that fails here hands back
        an error for its domains, exactly as it would one at a time.
        """
        ready: dict[str, dict[str, CheckOutcome]] = {}
        for checker in self.checkers:
            if not isinstance(checker, BatchChecker):
                continue
            try:
                outcomes = await checker.check_many(names)
            except Exception as exc:
                log.exception("checker.batch_crashed", checker=checker.name)
                message = f"{type(exc).__name__}: {exc}"
                outcomes = {n: CheckOutcome.failure(checker.name, message) for n in names}
            for name, outcome in outcomes.items():
                ready.setdefault(name, {})[checker.name] = outcome
        return ready

    async def scan_domain(
        self, domain_id: int, ready: dict[str, CheckOutcome] | None = None
    ) -> ScanReport | None:
        async with self._semaphore:
            return await self._scan_domain(domain_id, ready)

    async def _scan_domain(
        self, domain_id: int, ready: dict[str, CheckOutcome] | None = None
    ) -> ScanReport | None:
        async with session_scope() as session:
            domain = await session.get(Domain, domain_id)
            if domain is None:
                return None
            name = domain.name
            source = domain.source
            owner = domain.owner
            external_status = domain.external_status
            started = datetime.now(UTC)

        outcomes = excuse_missing_dns(
            await self._run_checkers(name, ready), source, external_status
        )
        # What the platform says comes first: it knows about a ban before DNS does.
        from_source = check_source_status(source, external_status)
        if from_source is not None:
            outcomes.insert(0, from_source)
        verdict = aggregate_verdict(outcomes)
        finished = datetime.now(UTC)

        async with session_scope() as session:
            # Locked for the read-modify-write: two scans of the same domain can
            # overlap (the hourly job and /scan_now), and without this both would
            # compare against the stale verdict and both would alert.
            domain = await session.get(Domain, domain_id, with_for_update=True)
            if domain is None:
                return None
            previous = domain.current_verdict
            changed = verdict != previous
            scan = Scan(
                domain_id=domain_id,
                verdict=verdict,
                previous_verdict=previous,
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
            await session.flush()
            domain.current_verdict = verdict
            domain.last_scanned_at = finished

        report = ScanReport(
            domain=name,
            verdict=verdict,
            previous_verdict=previous,
            changed=changed,
            outcomes=outcomes,
            domain_id=domain_id,
            scan_id=scan.id,
            source=source,
            owner=owner,
            finished_at=finished,
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
        async with session_scope() as session:
            rows = await session.execute(
                select(Domain.id, Domain.name).where(Domain.id.in_(domain_ids))
            )
            names = dict(rows.all())
        ready = await self._batch_outcomes(sorted(set(names.values())))
        results = await asyncio.gather(
            *(
                self.scan_domain(i, ready.get(names.get(i, ""), {}))
                for i in domain_ids
            )
        )
        return [r for r in results if r is not None]


async def collect_monitored_domain_ids() -> list[int]:
    """Every domain that gets checked: still in its source and not muted by hand."""
    async with session_scope() as session:
        return await DomainRepository(session).monitored_ids()


async def scan_source_statuses() -> list[ScanReport]:
    """Turn freshly synced platform statuses into verdicts, with no network calls.

    A ban shows up in the platform long before DNS or Safe Browsing notice it, and
    the status is already in the database after a sync -- so this runs as often as
    the sync does, instead of waiting for the hourly scan.

    It only ever raises a verdict. A domain that is fine in the platform says
    nothing about the blocklists, so a status pass must not undo what a real scan
    found; it writes a scan (and alerts) only for domains the status makes worse.
    """
    reports: list[ScanReport] = []
    async with session_scope() as session:
        domains = await DomainRepository(session).monitored()
        pending = [
            (d.id, outcome)
            for d in domains
            if (outcome := check_source_status(d.source, d.external_status)) is not None
            and outcome.verdict.severity > d.current_verdict.severity
        ]

    for domain_id, outcome in pending:
        now = datetime.now(UTC)
        async with session_scope() as session:
            # Same lock as a full scan: the hourly run may be scanning this very
            # domain, and only one of the two should report the change.
            domain = await session.get(Domain, domain_id, with_for_update=True)
            if domain is None or outcome.verdict.severity <= domain.current_verdict.severity:
                continue
            previous = domain.current_verdict
            scan = Scan(
                domain_id=domain_id,
                verdict=outcome.verdict,
                previous_verdict=previous,
                changed=True,
                started_at=now,
                finished_at=now,
                checks=[
                    ScanCheck(
                        checker=outcome.checker,
                        verdict=outcome.verdict,
                        summary=outcome.summary,
                        raw=outcome.raw or None,
                        created_at=now,
                    )
                ],
            )
            session.add(scan)
            await session.flush()
            domain.current_verdict = outcome.verdict
            reports.append(
                ScanReport(
                    domain=domain.name,
                    verdict=outcome.verdict,
                    previous_verdict=previous,
                    changed=True,
                    outcomes=[outcome],
                    domain_id=domain_id,
                    scan_id=scan.id,
                    source=domain.source,
                    owner=domain.owner,
                    finished_at=now,
                )
            )
    if reports:
        log.info("scan.statuses", changed=len(reports))
    return reports


async def mark_alert_sent(scan_id: int) -> None:
    """Record that the group actually heard about this scan.

    Written after delivery, not before: `scans.alert_sent` used to stay false on
    every row, which made it useless for answering "did anyone get told?"
    """
    async with session_scope() as session:
        scan = await session.get(Scan, scan_id)
        if scan is not None:
            scan.alert_sent = True
