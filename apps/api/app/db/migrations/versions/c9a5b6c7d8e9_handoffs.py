"""Hand-offs to a person

Phase 9.5. One row each time a conversation was handed to a person: the
summary built from it, how the person was told (an email with a link, or a
ticket through a declared HTTP action), and whether that went through.

Revision ID: c9a5b6c7d8e9
Revises: c9a4b5c6d7e8"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9a5b6c7d8e9"
down_revision: str | None = "c9a4b5c6d7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "handoffs",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("assistant_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("sent", "failed", name="handoff_status", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column(
            "channel",
            sa.Enum("email", "action", name="handoff_channel", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("ticket_ref", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["assistant_id"],
            ["assistants.id"],
            name=op.f("fk_handoffs_assistant_id_assistants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_handoffs_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_handoffs_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name=op.f("fk_handoffs_requested_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_handoffs")),
    )
    with op.batch_alter_table("handoffs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_handoffs_assistant_id"), ["assistant_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_handoffs_conversation_id"), ["conversation_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_handoffs_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_handoffs_org_id"), ["org_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("handoffs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_handoffs_org_id"))
        batch_op.drop_index(batch_op.f("ix_handoffs_created_at"))
        batch_op.drop_index(batch_op.f("ix_handoffs_conversation_id"))
        batch_op.drop_index(batch_op.f("ix_handoffs_assistant_id"))
    op.drop_table("handoffs")
