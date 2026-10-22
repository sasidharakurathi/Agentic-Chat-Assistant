"""Knowledge groups, access epochs and share links

Phase 9.4. Who may read each source (everyone, or groups; sources from
before stay everyone's), the content its builder confirmed despite the
ingest scan, the access epoch on organisations and conversations, and
read-only conversation share links.

Revision ID: c9a4b5c6d7e8
Revises: c9a3b4c5d6e7"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9a4b5c6d7e8"
down_revision: str | None = "c9a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine:  # type: ignore[type-arg]
    return sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("data_sources", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("everyone", sa.Boolean(), server_default=sa.text("true"), nullable=False)
        )
        batch_op.add_column(
            sa.Column("group_ids", _json(), server_default=sa.text("'[]'"), nullable=False)
        )
        batch_op.add_column(
            sa.Column("sensitive_confirmed_checksum", sa.String(length=128), nullable=True)
        )
    with op.batch_alter_table("organizations", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("access_epoch", sa.Integer(), server_default="0", nullable=False)
        )
    with op.batch_alter_table("conversations", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("access_epoch", sa.Integer(), server_default="0", nullable=False)
        )
    op.create_table(
        "conversation_shares",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("open_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_conversation_shares_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_conversation_shares_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_conversation_shares_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_shares")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_conversation_shares_token_hash")),
    )
    with op.batch_alter_table("conversation_shares", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_conversation_shares_conversation_id"), ["conversation_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_conversation_shares_org_id"), ["org_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("conversation_shares", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_conversation_shares_org_id"))
        batch_op.drop_index(batch_op.f("ix_conversation_shares_conversation_id"))
    op.drop_table("conversation_shares")
    with op.batch_alter_table("conversations", schema=None) as batch_op:
        batch_op.drop_column("access_epoch")
    with op.batch_alter_table("organizations", schema=None) as batch_op:
        batch_op.drop_column("access_epoch")
    with op.batch_alter_table("data_sources", schema=None) as batch_op:
        batch_op.drop_column("sensitive_confirmed_checksum")
        batch_op.drop_column("group_ids")
        batch_op.drop_column("everyone")
