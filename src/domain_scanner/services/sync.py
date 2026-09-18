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
    # No longer reported by the source — they stop being checked.
    removed: int = 0
    # Domains another source already owns — left untouched.
    foreign: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _apply(domain: Domain, item: SourceDomain, source: DomainSource) -> None:
    domain.source = source
    # Reported by the source = checked. The platform status is kept for reference.
    domain.is_active = True
    domain.external_status = item.status
    domain.external_parent_id = item.external_parent_id
    if item.external_id:
        domain.external_id = item.external_id


class DomainSyncService:
    """Pulls domain lists from every configured platform and reconciles the DB.

    Every domain a source reports is checked. Each source owns its rows: a sync only
    updates domains whose `source` matches, and only its own domains stop being
    checked when they disappear from it — PWApartners and SkakApp never touch each
    other's domains. Manually added domains are adopted by the first source that
    reports them, and a domain its owner dropped is taken over by any other source
    that still reports it.
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
                    deactivated=result.removed,
                    error=result.error,
                )
            )

        log.info(
            "sync.done",
            source=provider.source.value,
            fetched=result.fetched,
            created=result.created,
            updated=result.updated,
            removed=result.removed,
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

                # Another source owns it and still reports it — leave it be. If that
                # source has dropped it, this one takes it over so it keeps being checked.
                if domain.source not in (source, DomainSource.MANUAL) and domain.is_active:
                    result.foreign += 1
                    continue

                _apply(domain, item, source)
                touched.add(domain.id)
                result.updated += 1

            # This source no longer reports these domains — stop checking them.
            for domain in rows:
                if domain.source == source and domain.is_active and domain.id not in touched:
                    domain.is_active = False
                    result.removed += 1
