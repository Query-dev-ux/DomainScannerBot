"""who the domain belongs to in the source platform

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-25

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("domains", sa.Column("owner", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("domains", "owner")
