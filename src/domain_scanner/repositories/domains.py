from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from domain_scanner.db.models import Domain, DomainSource, Verdict


class DomainRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_name(self, name: str) -> Domain | None:
        return await self._session.scalar(
            select(Domain).where(Domain.name == name.strip().lower())
        )

    async def get_by_pwa_uuid(self, uuid: str) -> Domain | None:
        return await self._session.scalar(select(Domain).where(Domain.pwa_uuid == uuid))

    async def list_all(self) -> Sequence[Domain]:
        return (await self._session.scalars(select(Domain).order_by(Domain.name))).all()

    async def list_monitored(self) -> Sequence[Domain]:
        stmt = (
            select(Domain)
            .where(Domain.monitoring_enabled.is_(True), Domain.is_active.is_(True))
            .order_by(Domain.last_scanned_at.asc().nulls_first())
        )
        return (await self._session.scalars(stmt)).all()

    def _due_filter(self, older_than: timedelta):
        cutoff = datetime.now(UTC) - older_than
        return (
            Domain.monitoring_enabled.is_(True),
            Domain.is_active.is_(True),
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

    async def counts_by_verdict(self) -> dict[Verdict, int]:
        rows = await self._session.execute(
            select(Domain.current_verdict, func.count()).group_by(Domain.current_verdict)
        )
        return {verdict: count for verdict, count in rows}
