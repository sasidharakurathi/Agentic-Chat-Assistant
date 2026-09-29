"""runs.version_number

Conversations now use the assistant's current published version on every
turn instead of the one live when they started. Each run records which
version answered (null: the draft), so a change in behaviour can be traced
to a publish.

Revision ID: b6d8f0a2c469
Revises: a5c7e9f1b358
Create Date: 2026-09-28 13:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b6d8f0a2c469"
down_revision: str | None = "a5c7e9f1b358"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("version_number", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "version_number")
