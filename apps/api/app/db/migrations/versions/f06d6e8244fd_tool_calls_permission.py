"""tool_calls permission

How each tool call came to run, or not (task 4.7): "auto", "approved",
"declined", "expired", "interrupted" or "refused". The audit answer to
"who let this MCP tool do that?". Null for tools that never ask.

Revision ID: f06d6e8244fd
Revises: ef91aed450f4
Create Date: 2026-09-28 12:28:47.725319+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "f06d6e8244fd"
down_revision: str | None = "ef91aed450f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tool_calls", sa.Column("permission", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("tool_calls", "permission")
