"""Two-factor sign-in: authenticator secrets, recovery codes, strong sessions

Phase 8.4. Each person may have an authenticator app's secret (sealed with
the KEK) and ten single-use recovery codes (hashed). A refresh token
remembers whether its sign-in passed a second factor, so a renewed session
keeps that.

Revision ID: c8a4c1d2e3f4
Revises: c8a3b1c2d3e4"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8a4c1d2e3f4"
down_revision: str | None = "c8a3b1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_second_factors",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("dek_wrapped", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("enabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_step", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_second_factors_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_second_factors")),
    )
    with op.batch_alter_table("user_second_factors", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_user_second_factors_user_id"), ["user_id"], unique=True
        )
    op.create_table(
        "user_recovery_codes",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
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
            name=op.f("fk_user_recovery_codes_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_recovery_codes")),
        sa.UniqueConstraint("code_hash", name=op.f("uq_user_recovery_codes_code_hash")),
    )
    with op.batch_alter_table("user_recovery_codes", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_user_recovery_codes_user_id"), ["user_id"], unique=False
        )
    with op.batch_alter_table("refresh_tokens", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("strong", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("refresh_tokens", schema=None) as batch_op:
        batch_op.drop_column("strong")
    with op.batch_alter_table("user_recovery_codes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_user_recovery_codes_user_id"))
    op.drop_table("user_recovery_codes")
    with op.batch_alter_table("user_second_factors", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_user_second_factors_user_id"))
    op.drop_table("user_second_factors")
