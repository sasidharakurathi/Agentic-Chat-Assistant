"""Feedback on answers, and whether a conversation solved it

Phase 8.8. A thumbs (with an optional comment) per person per answer, and
one "Did this solve it?" outcome per conversation, with no name on it,
kept when the conversation is erased.

Revision ID: c8a7f2a3b4c5
Revises: c8a6e1f2a3b4"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8a7f2a3b4c5"
down_revision: str | None = "c8a6e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _times() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "message_feedback",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("assistant_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("rating", sa.String(length=8), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=True),
        *_times(),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["assistant_id"],
            ["assistants.id"],
            name=op.f("fk_message_feedback_assistant_id_assistants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_message_feedback_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name=op.f("fk_message_feedback_message_id_messages"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_message_feedback_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_message_feedback_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_message_feedback")),
        sa.UniqueConstraint("message_id", "user_id", name="uq_message_feedback_message_user"),
    )
    with op.batch_alter_table("message_feedback", schema=None) as batch_op:
        for column in ("assistant_id", "conversation_id", "message_id", "org_id", "user_id"):
            batch_op.create_index(
                batch_op.f(f"ix_message_feedback_{column}"), [column], unique=False
            )

    op.create_table(
        "conversation_outcomes",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("assistant_id", sa.Uuid(), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("version_number", sa.Integer(), nullable=True),
        sa.Column("solved", sa.Boolean(), nullable=False),
        *_times(),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["assistant_id"],
            ["assistants.id"],
            name=op.f("fk_conversation_outcomes_assistant_id_assistants"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_conversation_outcomes_conversation_id_conversations"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_conversation_outcomes_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_outcomes")),
        sa.UniqueConstraint("conversation_id", name="uq_conversation_outcomes_conversation"),
    )
    with op.batch_alter_table("conversation_outcomes", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_conversation_outcomes_assistant_id"), ["assistant_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_conversation_outcomes_org_id"), ["org_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("conversation_outcomes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_conversation_outcomes_org_id"))
        batch_op.drop_index(batch_op.f("ix_conversation_outcomes_assistant_id"))
    op.drop_table("conversation_outcomes")
    with op.batch_alter_table("message_feedback", schema=None) as batch_op:
        for column in ("user_id", "org_id", "message_id", "conversation_id", "assistant_id"):
            batch_op.drop_index(batch_op.f(f"ix_message_feedback_{column}"))
    op.drop_table("message_feedback")
