"""Batched emails to approvers

Phase 9.2. `approver_notices` remembers when each approver was last emailed
about requests waiting for them, and since when more have waited untold,
so a burst of requests is one email rather than one each.

Revision ID: c9a2b3c4d5e6
Revises: c9a1b2c3d4e5"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9a2b3c4d5e6"
down_revision: str | None = "c9a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approver_notices",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pending_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_approver_notices_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_approver_notices_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approver_notices")),
        sa.UniqueConstraint("org_id", "user_id", name="uq_approver_notices_person"),
    )
    with op.batch_alter_table("approver_notices", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_approver_notices_org_id"), ["org_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_approver_notices_user_id"), ["user_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_approver_notices_pending_since"), ["pending_since"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("approver_notices", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_approver_notices_pending_since"))
        batch_op.drop_index(batch_op.f("ix_approver_notices_user_id"))
        batch_op.drop_index(batch_op.f("ix_approver_notices_org_id"))
    op.drop_table("approver_notices")
