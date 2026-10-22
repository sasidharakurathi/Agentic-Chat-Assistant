"""account recovery: one-time links, verified addresses

Phase 7b.6: password reset, change password and email verification. A
table of one-time links (hashed), and on each user when their address was
verified and when their password last changed. People who joined through
an invite to their address count as verified.

Revision ID: c7b6f1a2b3c4
Revises: c7b5d1e2f3a4"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7b6f1a2b3c4"
down_revision: str | None = "c7b5d1e2f3a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True)
        )
    # An accepted invite was for their address: as good as a link opened.
    op.execute(
        "UPDATE users SET email_verified_at = ("
        "SELECT MIN(invites.accepted_at) FROM invites "
        "WHERE invites.email = users.email AND invites.accepted_at IS NOT NULL)"
    )
    op.create_table(
        "user_tokens",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=20), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_tokens")),
    )
    op.create_index(op.f("ix_user_tokens_user_id"), "user_tokens", ["user_id"], unique=False)
    op.create_index(op.f("ix_user_tokens_token_hash"), "user_tokens", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_user_tokens_token_hash"), table_name="user_tokens")
    op.drop_index(op.f("ix_user_tokens_user_id"), table_name="user_tokens")
    op.drop_table("user_tokens")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("password_changed_at")
        batch_op.drop_column("email_verified_at")
