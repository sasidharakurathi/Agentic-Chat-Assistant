"""How Chat uses your data: the notice and who accepted it

Phase 8.7. Each organisation's privacy notice, every saved revision kept,
and who accepted which version. Nothing is filled in: with no revision
saved, the built-in text is version 1, and nobody has accepted it yet.

Revision ID: c8a6e1f2a3b4
Revises: c8a5d1e2f3a4"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8a6e1f2a3b4"
down_revision: str | None = "c8a5d1e2f3a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "privacy_notices",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
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
            name=op.f("fk_privacy_notices_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_privacy_notices_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_privacy_notices")),
        sa.UniqueConstraint("org_id", "revision", name="uq_privacy_notices_org_revision"),
    )
    with op.batch_alter_table("privacy_notices", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_privacy_notices_org_id"), ["org_id"], unique=False)
    op.create_table(
        "notice_acceptances",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "accepted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_notice_acceptances_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_notice_acceptances_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notice_acceptances")),
        sa.UniqueConstraint(
            "org_id", "user_id", "version", name="uq_notice_acceptances_org_user_version"
        ),
    )
    with op.batch_alter_table("notice_acceptances", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_notice_acceptances_org_id"), ["org_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_notice_acceptances_user_id"), ["user_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("notice_acceptances", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_notice_acceptances_user_id"))
        batch_op.drop_index(batch_op.f("ix_notice_acceptances_org_id"))
    op.drop_table("notice_acceptances")
    with op.batch_alter_table("privacy_notices", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_privacy_notices_org_id"))
    op.drop_table("privacy_notices")
