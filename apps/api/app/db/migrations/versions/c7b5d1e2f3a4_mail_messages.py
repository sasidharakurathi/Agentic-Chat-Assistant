"""mail messages

Phase 7b.5: a record of each email the platform sent or tried to (who to,
what kind, its subject, its outcome), never the body.

Revision ID: c7b5d1e2f3a4
Revises: c7b4a1b2c3d4"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7b5d1e2f3a4"
down_revision: str | None = "c7b4a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mail_messages",
        sa.Column("org_id", sa.Uuid(), nullable=True),
        sa.Column("to_address", sa.String(length=320), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(length=300), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_mail_messages_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mail_messages")),
    )
    op.create_index(op.f("ix_mail_messages_org_id"), "mail_messages", ["org_id"], unique=False)
    op.create_index(op.f("ix_mail_messages_status"), "mail_messages", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_mail_messages_status"), table_name="mail_messages")
    op.drop_index(op.f("ix_mail_messages_org_id"), table_name="mail_messages")
    op.drop_table("mail_messages")
