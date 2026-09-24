"""conversations.external_user_ref

Plan §2.5 specifies `conversations.external_user_ref`: an identifier the
embedding application supplies for its own end user, so it can list that
user's conversations. It was never added.

Revision ID: e3f5a7b9c125
Revises: d8b2c4e6f013
Create Date: 2026-09-24 01:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e3f5a7b9c125"
down_revision: str | None = "d8b2c4e6f013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("external_user_ref", sa.String(length=200), nullable=True))
        batch.create_index(
            batch.f("ix_conversations_external_user_ref"), ["external_user_ref"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.drop_index(batch.f("ix_conversations_external_user_ref"))
        batch.drop_column("external_user_ref")
