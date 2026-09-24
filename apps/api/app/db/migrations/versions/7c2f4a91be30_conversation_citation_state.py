"""conversation citation state

Carries the ``[n]`` markers already handed out in a conversation across
turns. Needed because the SDK session is resumed: the model's context still
holds earlier turns' numbered ``kb_search`` results, so a marker has to keep
meaning the same chunk for as long as that context does.

Revision ID: 7c2f4a91be30
Revises: 4d19bec6d86b
Create Date: 2026-09-21 00:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7c2f4a91be30"
down_revision: str | None = "4d19bec6d86b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "citation_state",
            # Same spelling every other JSONB column in this project's
            # migrations uses (app/db/types.py's JSONB is a configured
            # *instance*, not a class, so it can't be called here).
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
            # Existing rows predate citations; an empty map means "nothing
            # handed out yet", which is exactly right for them.
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("conversations", "citation_state")
