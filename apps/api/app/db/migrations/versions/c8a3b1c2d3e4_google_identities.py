"""Google sign-in: linked identities, and accounts without a password

Phase 8.3. `user_identities` keys an outside sign-in on the provider's own
id for the person (Google's `sub`), one per provider per account. An
account made through Google has no password, so `users.password_hash`
may be empty.

Revision ID: c8a3b1c2d3e4
Revises: c7b8a1b2c3d4"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8a3b1c2d3e4"
down_revision: str | None = "c7b8a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: What a Google-only account gets on the way down: a value no password
#: hashes to, so it stays closed to password sign-in.
NO_PASSWORD = "!google-only"


def upgrade() -> None:
    op.create_table(
        "user_identities",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=False),
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
            name=op.f("fk_user_identities_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_identities")),
        sa.UniqueConstraint("provider", "subject", name="uq_user_identities_provider_subject"),
        sa.UniqueConstraint("user_id", "provider", name="uq_user_identities_user_provider"),
    )
    with op.batch_alter_table("user_identities", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_user_identities_user_id"), ["user_id"], unique=False)
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("password_hash", existing_type=sa.String(length=255), nullable=True)


def downgrade() -> None:
    op.execute(f"UPDATE users SET password_hash = '{NO_PASSWORD}' WHERE password_hash IS NULL")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("password_hash", existing_type=sa.String(length=255), nullable=False)
    with op.batch_alter_table("user_identities", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_user_identities_user_id"))
    op.drop_table("user_identities")
