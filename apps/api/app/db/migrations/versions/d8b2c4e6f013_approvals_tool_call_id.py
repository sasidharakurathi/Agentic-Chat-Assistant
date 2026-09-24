"""approvals.tool_call_id

Plan §2.5 specifies a nullable `tool_call_id` on approvals: the SDK's
`tool_use_id` for the call being gated, so an approval can be joined to the
tool call it held up in the trace. It was never added.

Revision ID: d8b2c4e6f013
Revises: c5a1f7e2d904
Create Date: 2026-09-24 00:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8b2c4e6f013"
down_revision: str | None = "c5a1f7e2d904"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("approvals") as batch:
        batch.add_column(sa.Column("tool_call_id", sa.String(length=100), nullable=True))
        batch.create_index(batch.f("ix_approvals_tool_call_id"), ["tool_call_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("approvals") as batch:
        batch.drop_index(batch.f("ix_approvals_tool_call_id"))
        batch.drop_column("tool_call_id")
