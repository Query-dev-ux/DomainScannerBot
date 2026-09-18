from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from domain_scanner.db import session_scope
from domain_scanner.db.models import Domain, DomainSource, SyncLog
from domain_scanner.logging import get_logger
from domain_scanner.sources import DomainProvider, SourceDomain, dedupe

log = get_logger(__name__)


@dataclass(slots=True)
class SyncResult:
    source: DomainSource
    title: str
    fetched: int = 0
    created: int = 0
    updated: int = 0
    deactivated: int = 0
    # Domains another source already owns — left untouched.
    foreign: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _apply(domain: Domain, item: SourceDomain, source: DomainSource) -> None:
    domain.source = source
    domain.is_active = item.is_active
    domain.external_status = item.status
    domain.external_parent_id = item.external_parent_id
    if item.external_id:
        domain.external_id = item.external_id


class DomainSyncService:
    """Pulls domain lists from every configured platform and reconciles the DB.

    Each source owns its rows: a sync only updates or deactivates domains whose
    `source` matches, so PWA.partners and UClient never switch off each other's
    domains. Manually added domains are adopted by the first source that reports
    them.
    """

    def __init__(self, providers: list[DomainProvider]) -> None:
        self.providers = providers

    async def run(self) -> list[SyncResult]:
        return [await self.run_source(p) for p in self.providers]

    async def run_source(self, provider: DomainProvider) -> SyncResult:
        started = datetime.now(UTC)
        result = SyncResult(source=provider.source, title=provider.title)
        try:
            items = dedupe(await provider.fetch_domains())
            result.fetched = len(items)
            await self._reconcile(provider.source, items, result)
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            log.exception("sync.failed", source=provider.source.value)

        async with session_scope() as session:
            session.add(
                SyncLog(
                    source=provider.source,
                    started_at=started,
                    finished_at=datetime.now(UTC),
                    fetched=result.fetched,
                    created=result.created,
                    updated=result.updated,
                    deactivated=result.deactivated,
                    error=result.error,
                )
            )

        log.info(
            "sync.done",
            source=provider.source.value,
            fetched=result.fetched,
            created=result.created,
            updated=result.updated,
            deactivated=result.deactivated,
            foreign=result.foreign,
            error=result.error,
        )
        return result

    async def _reconcile(
        self, source: DomainSource, items: list[SourceDomain], result: SyncResult
    ) -> None:
        async with session_scope() as session:
            rows = (await session.scalars(select(Domain))).all()
            by_name = {d.name: d for d in rows}
            by_external = {
                d.external_id: d for d in rows if d.source == source and d.external_id
            }
            touched: set[int] = set()

            for item in items:
                domain = (by_external.get(item.external_id) if item.external_id else None) or (
                    by_name.get(item.name)
                )

                if domain is None:
                    domain = Domain(name=item.name, source=source)
                    _apply(domain, item, source)
                    session.add(domain)
                    by_name[item.name] = domain
                    result.created += 1
                    continue

                if domain.source not in (source, DomainSource.MANUAL):
                    result.foreign += 1
                    continue

                _apply(domain, item, source)
                touched.add(domain.id)
                result.updated += 1

            # This source no longer reports these domains — stop checking them.
            for domain in rows:
                if domain.source == source and domain.is_active and domain.id not in touched:
                    domain.is_active = False
                    result.deactivated += 1
