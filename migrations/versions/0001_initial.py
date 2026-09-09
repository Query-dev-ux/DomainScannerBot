"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-09

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

verdict_enum = postgresql.ENUM(
    "unknown", "clean", "suspicious", "flagged", "error", name="verdict", create_type=False
)
domain_source_enum = postgresql.ENUM(
    "pwa", "manual", name="domain_source", create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()
    verdict_enum.create(bind, checkfirst=True)
    domain_source_enum.create(bind, checkfirst=True)

    op.create_table(
        "domains",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("source", domain_source_enum, nullable=False),
        sa.Column("pwa_uuid", sa.String(length=64), nullable=True),
        sa.Column("pwa_status", sa.Integer(), nullable=True),
        sa.Column("pwa_pwa_uuid", sa.String(length=64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "monitoring_enabled", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("current_verdict", verdict_enum, nullable=False, server_default="unknown"),
        sa.Column("last_scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_domains"),
        sa.UniqueConstraint("name", name="uq_domains_name"),
        sa.UniqueConstraint("pwa_uuid", name="uq_domains_pwa_uuid"),
    )
    op.create_index("ix_domains_last_scanned_at", "domains", ["last_scanned_at"])

    op.create_table(
        "scans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("domain_id", sa.Integer(), nullable=False),
        sa.Column("verdict", verdict_enum, nullable=False),
        sa.Column("previous_verdict", verdict_enum, nullable=False, server_default="unknown"),
        sa.Column("changed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("alert_sent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["domain_id"], ["domains.id"], name="fk_scans_domain_id_domains", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_scans"),
    )
    op.create_index(
        "ix_scans_domain_id_created_at", "scans", ["domain_id", "created_at"]
    )

    op.create_table(
        "scan_checks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scan_id", sa.Integer(), nullable=False),
        sa.Column("checker", sa.String(length=64), nullable=False),
        sa.Column("verdict", verdict_enum, nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["scan_id"], ["scans.id"], name="fk_scan_checks_scan_id_scans", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_scan_checks"),
    )
    op.create_index("ix_scan_checks_scan_id", "scan_checks", ["scan_id"])

    op.create_table(
        "sync_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("deactivated", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_sync_logs"),
    )


def downgrade() -> None:
    op.drop_table("sync_logs")
    op.drop_index("ix_scan_checks_scan_id", table_name="scan_checks")
    op.drop_table("scan_checks")
    op.drop_index("ix_scans_domain_id_created_at", table_name="scans")
    op.drop_table("scans")
    op.drop_index("ix_domains_last_scanned_at", table_name="domains")
    op.drop_table("domains")
    bind = op.get_bind()
    domain_source_enum.drop(bind, checkfirst=True)
    verdict_enum.drop(bind, checkfirst=True)
