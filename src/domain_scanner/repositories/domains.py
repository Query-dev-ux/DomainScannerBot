from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import func, not_, select
from sqlalchemy.ext.asyncio import AsyncSession

from domain_scanner.db.models import Domain, DomainSource, Scan, ScanCheck, Verdict

# is_active = the domain is still reported by its source (see Domain.is_active).
_PRESENT = Domain.is_active.is_(True)
_MONITORED = (_PRESENT, Domain.monitoring_enabled.is_(True))


@dataclass(slots=True)
class DomainWithChecks:
    """A domain plus the per-checker verdicts of its most recent scan."""

    domain: Domain
    checks: dict[str, Verdict] = field(default_factory=dict)


@dataclass(slots=True)
class DomainStats:
    """Counts over monitored domains, plus how many were muted by hand."""

    by_verdict: dict[Verdict, int] = field(default_factory=dict)
    by_source: dict[DomainSource, int] = field(default_factory=dict)
    muted: int = 0

    @property
    def monitored(self) -> int:
        return sum(self.by_verdict.values())


class DomainRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, domain_id: int) -> Domain | None:
        return await self._session.get(Domain, domain_id)

    async def get_by_name(self, name: str) -> Domain | None:
        return await self._session.scalar(
            select(Domain).where(Domain.name == name.strip().lower())
        )

    async def list_all(self) -> Sequence[Domain]:
        return (await self._session.scalars(select(Domain).order_by(Domain.name))).all()

    async def list_for_display(
        self, verdicts: set[Verdict] | None = None
    ) -> list[DomainWithChecks]:
        """Domains their sources still report, each with its last scan's checks.

        The per-checker verdicts are what the tags in /list are built from: the
        aggregate verdict says a domain is bad, the checks say why.
        """
        stmt = select(Domain).where(_PRESENT).order_by(Domain.name)
        if verdicts is not None:
            stmt = stmt.where(Domain.current_verdict.in_(verdicts))
        domains = (await self._session.scalars(stmt)).all()
        if not domains:
            return []

        # One row per domain: the newest scan it has.
        latest_scans = (
            select(Scan.id)
            .where(Scan.domain_id.in_([d.id for d in domains]))
            .distinct(Scan.domain_id)
            .order_by(Scan.domain_id, Scan.id.desc())
        )
        rows = await self._session.execute(
            select(Scan.domain_id, ScanCheck.checker, ScanCheck.verdict)
            .join(ScanCheck, ScanCheck.scan_id == Scan.id)
            .where(Scan.id.in_(latest_scans))
        )
        by_domain: dict[int, dict[str, Verdict]] = {}
        for domain_id, checker, verdict in rows:
            by_domain.setdefault(domain_id, {})[checker] = verdict
        return [DomainWithChecks(d, by_domain.get(d.id, {})) for d in domains]

    async def monitored_ids(self) -> list[int]:
        stmt = select(Domain.id).where(*_MONITORED).order_by(Domain.id)
        return list((await self._session.scalars(stmt)).all())

    async def add_manual(self, name: str) -> tuple[Domain, bool]:
        name = name.strip().lower()
        existing = await self.get_by_name(name)
        if existing is not None:
            return existing, False
        domain = Domain(name=name, source=DomainSource.MANUAL, is_active=True)
        self._session.add(domain)
        await self._session.flush()
        return domain, True

    async def set_monitoring(self, domain_id: int, enabled: bool) -> Domain | None:
        domain = await self.get(domain_id)
        if domain is not None:
            domain.monitoring_enabled = enabled
        return domain

    async def stats(self) -> DomainStats:
        stats = DomainStats()
        rows = await self._session.execute(
            select(Domain.current_verdict, func.count())
            .where(*_MONITORED)
            .group_by(Domain.current_verdict)
        )
        stats.by_verdict = {v: n for v, n in rows}
        rows = await self._session.execute(
            select(Domain.source, func.count()).where(*_MONITORED).group_by(Domain.source)
        )
        stats.by_source = {s: n for s, n in rows}
        stats.muted = int(
            await self._session.scalar(
                select(func.count())
                .select_from(Domain)
                .where(_PRESENT, not_(Domain.monitoring_enabled))
            )
            or 0
        )
        return stats
