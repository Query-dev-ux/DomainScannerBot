from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from domain_scanner.db.models import Domain, DomainSource, Verdict

# is_active = the domain is still reported by its source (see Domain.is_active).
_PRESENT = Domain.is_active.is_(True)
_MONITORED = (_PRESENT, Domain.monitoring_enabled.is_(True))

# What the bot calls a bad domain.
BAD_VERDICTS = (Verdict.FLAGGED, Verdict.SUSPICIOUS)


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

    async def list_for_display(self, verdicts: set[Verdict] | None = None) -> Sequence[Domain]:
        """Domains their sources still report (muted ones included)."""
        stmt = select(Domain).where(_PRESENT).order_by(Domain.name)
        if verdicts is not None:
            stmt = stmt.where(Domain.current_verdict.in_(verdicts))
        return (await self._session.scalars(stmt)).all()

    def _due_filter(self, older_than: timedelta):
        cutoff = datetime.now(UTC) - older_than
        return (
            *_MONITORED,
            or_(Domain.last_scanned_at.is_(None), Domain.last_scanned_at < cutoff),
        )

    async def list_due_for_scan(
        self, older_than: timedelta, limit: int | None = None
    ) -> Sequence[Domain]:
        """Domains that need a scan, never-scanned and stalest first."""
        stmt = (
            select(Domain)
            .where(*self._due_filter(older_than))
            .order_by(Domain.last_scanned_at.asc().nulls_first())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return (await self._session.scalars(stmt)).all()

    async def count_due_for_scan(self, older_than: timedelta) -> int:
        stmt = select(func.count()).select_from(Domain).where(*self._due_filter(older_than))
        return int(await self._session.scalar(stmt) or 0)

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

    async def bad_domain_names(self) -> list[str]:
        """Monitored domains marked bad (зашкварен / подозрительно), worst first.

        Same population as the /status counts, so the list matches the summary.
        """
        rows = await self._session.execute(
            select(Domain.name, Domain.current_verdict).where(
                *_MONITORED, Domain.current_verdict.in_(BAD_VERDICTS)
            )
        )
        return [
            name
            for name, _ in sorted(rows, key=lambda r: (-r[1].severity, r[0]))
        ]

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
