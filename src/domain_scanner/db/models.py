from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from domain_scanner.db.base import Base, TimestampMixin


class Verdict(str, enum.Enum):
    """Aggregated reputation state, ordered from best to worst."""

    UNKNOWN = "unknown"
    CLEAN = "clean"
    SUSPICIOUS = "suspicious"
    FLAGGED = "flagged"
    ERROR = "error"

    @property
    def severity(self) -> int:
        return _VERDICT_SEVERITY[self]


_VERDICT_SEVERITY = {
    Verdict.UNKNOWN: 0,
    Verdict.CLEAN: 1,
    Verdict.ERROR: 2,
    Verdict.SUSPICIOUS: 3,
    Verdict.FLAGGED: 4,
}


class DomainSource(str, enum.Enum):
    PWA = "pwa"
    MANUAL = "manual"


# Single shared enum instances so each PG type is emitted/created exactly once.
_values = lambda e: [m.value for m in e]  # noqa: E731
verdict_enum = Enum(Verdict, name="verdict", values_callable=_values)
domain_source_enum = Enum(DomainSource, name="domain_source", values_callable=_values)


class Domain(TimestampMixin, Base):
    __tablename__ = "domains"
    __table_args__ = (
        UniqueConstraint("name", name="uq_domains_name"),
        Index("ix_domains_last_scanned_at", "last_scanned_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[DomainSource] = mapped_column(
        domain_source_enum, default=DomainSource.PWA, nullable=False
    )

    # PWA.partners linkage
    pwa_uuid: Mapped[str | None] = mapped_column(String(64), unique=True)
    pwa_status: Mapped[int | None] = mapped_column()
    pwa_pwa_uuid: Mapped[str | None] = mapped_column(String(64))

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    monitoring_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    current_verdict: Mapped[Verdict] = mapped_column(
        verdict_enum, default=Verdict.UNKNOWN, nullable=False
    )
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    scans: Mapped[list[Scan]] = relationship(
        back_populates="domain", cascade="all, delete-orphan", order_by="Scan.id.desc()"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Domain {self.name} verdict={self.current_verdict.value}>"


class Scan(TimestampMixin, Base):
    """A single reputation scan run for a domain."""

    __tablename__ = "scans"
    __table_args__ = (Index("ix_scans_domain_id_created_at", "domain_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    domain_id: Mapped[int] = mapped_column(
        ForeignKey("domains.id", ondelete="CASCADE"), nullable=False
    )

    verdict: Mapped[Verdict] = mapped_column(verdict_enum, nullable=False)
    previous_verdict: Mapped[Verdict] = mapped_column(
        verdict_enum, default=Verdict.UNKNOWN, nullable=False
    )
    changed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    alert_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    domain: Mapped[Domain] = relationship(back_populates="scans")
    checks: Mapped[list[ScanCheck]] = relationship(
        back_populates="scan", cascade="all, delete-orphan"
    )


class ScanCheck(Base):
    """Result of one checker within a scan."""

    __tablename__ = "scan_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(
        ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
    )
    checker: Mapped[str] = mapped_column(String(64), nullable=False)
    verdict: Mapped[Verdict] = mapped_column(verdict_enum, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    scan: Mapped[Scan] = relationship(back_populates="checks")


class SyncLog(Base):
    """Audit trail for PWA domain-list synchronisations."""

    __tablename__ = "sync_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    created: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    updated: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    deactivated: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
