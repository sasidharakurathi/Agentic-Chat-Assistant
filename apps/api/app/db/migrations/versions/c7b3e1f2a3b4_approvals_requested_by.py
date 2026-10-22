"""approvals record who asked

Phase 7b.3, separation of duties. Each tool-call approval names who asked
(the person whose conversation raised it), so its decision can say whether
the asker decided their own request, and so the record survives the
conversation. Existing approvals are filled in from their conversations.

Revision ID: c7b3e1f2a3b4
Revises: c7b1d4e5f6a7"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7b3e1f2a3b4"
down_revision: str | None = "c7b1d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.add_column(sa.Column("requested_by", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f("fk_approvals_requested_by_users"),
            "users",
            ["requested_by"],
            ["id"],
            ondelete="SET NULL",
        )
    op.execute(
        "UPDATE approvals SET requested_by = ("
        "SELECT created_by FROM conversations WHERE conversations.id = approvals.conversation_id)"
    )


def downgrade() -> None:
    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_approvals_requested_by_users"), type_="foreignkey")
        batch_op.drop_column("requested_by")
