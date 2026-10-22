"""Erasing for good, and legal holds

Phase 8.10. `erasures` records each purge (by retention, or erasing one
person) with the conversation ids, to re-apply after a restore. `legal_holds`
keeps a person's or a conversation's data out of every purge.

Revision ID: c8a9b4c5d6e7
Revises: c8a8a3b4c5d6"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8a9b4c5d6e7"
down_revision: str | None = "c8a8a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "erasures",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("cutoff", sa.DateTime(timezone=True), nullable=True),
        sa.Column("conversation_ids", sa.JSON(), nullable=False),
        sa.Column("memories", sa.Integer(), nullable=False),
        sa.Column("comments", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_erasures_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_erasures_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_erasures")),
    )
    with op.batch_alter_table("erasures", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_erasures_org_id"), ["org_id"], unique=False)

    op.create_table(
        "legal_holds",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_legal_holds_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_legal_holds_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_legal_holds_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["released_by"],
            ["users.id"],
            name=op.f("fk_legal_holds_released_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_legal_holds_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_legal_holds")),
    )
    with op.batch_alter_table("legal_holds", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_legal_holds_conversation_id"), ["conversation_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_legal_holds_org_id"), ["org_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_legal_holds_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("legal_holds", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_legal_holds_user_id"))
        batch_op.drop_index(batch_op.f("ix_legal_holds_org_id"))
        batch_op.drop_index(batch_op.f("ix_legal_holds_conversation_id"))
    op.drop_table("legal_holds")
    with op.batch_alter_table("erasures", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_erasures_org_id"))
    op.drop_table("erasures")
