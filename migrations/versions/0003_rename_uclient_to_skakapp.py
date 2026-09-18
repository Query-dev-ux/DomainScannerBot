"""rename domain source 'uclient' to 'skakapp'

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-18

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Renames the label in place: rows already tagged 'uclient' become 'skakapp'.
    op.execute("ALTER TYPE domain_source RENAME VALUE 'uclient' TO 'skakapp'")


def downgrade() -> None:
    op.execute("ALTER TYPE domain_source RENAME VALUE 'skakapp' TO 'uclient'")
