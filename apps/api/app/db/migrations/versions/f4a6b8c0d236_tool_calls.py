"""tool_calls

Plan §2.5 specifies a `tool_calls` table (one row per tool call: name,
server, input, output, status, latency). Tool calls existed only inside the
assistant message's `blocks` JSON, shaped for the UI and not queryable.

Revision ID: f4a6b8c0d236
Revises: e3f5a7b9c125
Create Date: 2026-09-24 02:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f4a6b8c0d236"
down_revision: str | None = "e3f5a7b9c125"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")
    op.create_table(
        "tool_calls",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("call_id", sa.String(length=100), nullable=True),
        sa.Column("tool_name", sa.String(length=200), nullable=False),
        sa.Column("server", sa.String(length=100), nullable=False),
        sa.Column("input", json_type, nullable=False),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_tool_calls_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name=op.f("fk_tool_calls_message_id_messages"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_tool_calls_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tool_calls")),
    )
    for column in ("call_id", "conversation_id", "message_id", "org_id", "tool_name"):
        op.create_index(op.f(f"ix_tool_calls_{column}"), "tool_calls", [column], unique=False)


def downgrade() -> None:
    for column in ("tool_name", "org_id", "message_id", "conversation_id", "call_id"):
        op.drop_index(op.f(f"ix_tool_calls_{column}"), table_name="tool_calls")
    op.drop_table("tool_calls")
