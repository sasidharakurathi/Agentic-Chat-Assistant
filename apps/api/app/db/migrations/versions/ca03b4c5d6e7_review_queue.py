"""The needs-review queue, and what an erasure took with it

Phase 10.3: `review_items` holds a builder's decision on an answer that
needed review (turned into a test case, or dismissed), and whether the
person who asked was told it was fixed. Open items have no row: they are
derived from feedback and failed turns.

Phase 10.5: `erasures.details` counts what went with the conversations
(messages, run traces, tool calls, summaries, share links, review
decisions).

Revision ID: ca03b4c5d6e7
Revises: c9a6b7c8d9e0"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ca03b4c5d6e7"
down_revision: str | None = "c9a6b7c8d9e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_items",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("assistant_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "open",
                "turned_into_case",
                "dismissed",
                name="review_state",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("eval_case_id", sa.Uuid(), nullable=True),
        sa.Column("dismissed_reason", sa.String(length=500), nullable=True),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fixed_version_number", sa.Integer(), nullable=True),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
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
            name=op.f("fk_review_items_assistant_id_assistants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["users.id"],
            name=op.f("fk_review_items_decided_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["eval_case_id"],
            ["eval_cases.id"],
            name=op.f("fk_review_items_eval_case_id_eval_cases"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name=op.f("fk_review_items_message_id_messages"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_review_items_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_items")),
        sa.UniqueConstraint("message_id", name="uq_review_items_message_id"),
    )
    with op.batch_alter_table("review_items", schema=None) as batch_op:
        for column in ("assistant_id", "eval_case_id", "org_id"):
            batch_op.create_index(batch_op.f(f"ix_review_items_{column}"), [column], unique=False)

    with op.batch_alter_table("erasures", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("details", sa.JSON(), server_default=sa.text("'{}'"), nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("erasures", schema=None) as batch_op:
        batch_op.drop_column("details")
    with op.batch_alter_table("review_items", schema=None) as batch_op:
        for column in ("org_id", "eval_case_id", "assistant_id"):
            batch_op.drop_index(batch_op.f(f"ix_review_items_{column}"))
    op.drop_table("review_items")
