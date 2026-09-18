"""multiple domain sources: UClient + generic external_* linkage

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-18

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

domain_source_enum = postgresql.ENUM(name="domain_source", create_type=False)


def upgrade() -> None:
    # ADD VALUE must not share a transaction with statements that use the value;
    # run it on its own so this also works on PostgreSQL < 12.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE domain_source ADD VALUE IF NOT EXISTS 'uclient'")

    op.add_column("domains", sa.Column("external_id", sa.String(length=128), nullable=True))
    op.add_column(
        "domains", sa.Column("external_parent_id", sa.String(length=128), nullable=True)
    )
    op.add_column("domains", sa.Column("external_status", sa.String(length=32), nullable=True))

    # Carry the PWA.partners linkage over before dropping the old columns.
    op.execute(
        """
        UPDATE domains
           SET external_id = pwa_uuid,
               external_parent_id = pwa_pwa_uuid,
               external_status = pwa_status::text
        """
    )

    op.drop_constraint("uq_domains_pwa_uuid", "domains", type_="unique")
    op.drop_column("domains", "pwa_uuid")
    op.drop_column("domains", "pwa_status")
    op.drop_column("domains", "pwa_pwa_uuid")
    op.create_index(
        "ix_domains_source_external_id", "domains", ["source", "external_id"]
    )

    op.add_column("sync_logs", sa.Column("source", domain_source_enum, nullable=True))
    # Every sync so far came from PWA.partners.
    op.execute("UPDATE sync_logs SET source = 'pwa'")


def downgrade() -> None:
    op.drop_column("sync_logs", "source")
    op.drop_index("ix_domains_source_external_id", table_name="domains")

    op.add_column("domains", sa.Column("pwa_uuid", sa.String(length=64), nullable=True))
    op.add_column("domains", sa.Column("pwa_status", sa.Integer(), nullable=True))
    op.add_column("domains", sa.Column("pwa_pwa_uuid", sa.String(length=64), nullable=True))
    op.execute(
        """
        UPDATE domains
           SET pwa_uuid = external_id,
               pwa_pwa_uuid = external_parent_id,
               pwa_status = CASE WHEN external_status ~ '^-?[0-9]+$'
                                 THEN external_status::integer END
         WHERE source = 'pwa'
        """
    )
    # PostgreSQL cannot drop an enum value; keep UClient rows but hand them over to
    # 'manual' so the pre-0002 code can still load them.
    op.execute("UPDATE domains SET source = 'manual' WHERE source = 'uclient'")
    op.create_unique_constraint("uq_domains_pwa_uuid", "domains", ["pwa_uuid"])

    op.drop_column("domains", "external_status")
    op.drop_column("domains", "external_parent_id")
    op.drop_column("domains", "external_id")
