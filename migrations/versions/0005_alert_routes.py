"""per-owner routing of alerts to Telegram chats

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-28

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The enum already exists (domains.source): reuse the type, do not re-create it.
domain_source_enum = postgresql.ENUM(name="domain_source", create_type=False)


def upgrade() -> None:
    op.create_table(
        "alert_routes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", domain_source_enum, nullable=False),
        sa.Column("owner", sa.String(length=64), nullable=False),
        sa.Column("owner_key", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_title", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_alert_routes"),
        sa.UniqueConstraint("source", "owner_key", name="uq_alert_routes_source_owner"),
    )


def downgrade() -> None:
    op.drop_table("alert_routes")
