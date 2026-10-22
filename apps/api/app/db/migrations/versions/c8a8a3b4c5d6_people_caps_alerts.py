"""Per-person spend, per-person caps, and alerts for admins

Phase 8.9. The usage ledger records whose spend each row is, filled in for
what is already there from the conversation it happened in (evals and
indexing stay the organisation's). Budgets can belong to one person. And
`admin_alerts` records what stopped Chat for everyone.

Revision ID: c8a8a3b4c5d6
Revises: c8a7f2a3b4c5"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8a8a3b4c5d6"
down_revision: str | None = "c8a7f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("usage_events", schema=None) as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f("fk_usage_events_user_id_users"),
            "users",
            ["user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_usage_events_org_user_created", ["org_id", "user_id", "created_at"], unique=False
        )
    # What is already there: whose conversation it was. Evals are nobody's.
    op.execute(
        "UPDATE usage_events SET user_id = ("
        " SELECT c.created_by FROM conversations c"
        " WHERE c.id = usage_events.conversation_id AND c.eval_run_id IS NULL"
        ") WHERE conversation_id IS NOT NULL"
    )

    with op.batch_alter_table("budgets", schema=None) as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f("fk_budgets_user_id_users"), "users", ["user_id"], ["id"], ondelete="CASCADE"
        )
        batch_op.create_index(batch_op.f("ix_budgets_user_id"), ["user_id"], unique=False)

    op.create_table(
        "admin_alerts",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("subject", sa.String(length=80), nullable=False),
        sa.Column("detail", sa.String(length=500), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column(
            "first_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "last_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("emailed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_admin_alerts_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by"],
            ["users.id"],
            name=op.f("fk_admin_alerts_resolved_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_alerts")),
    )
    with op.batch_alter_table("admin_alerts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_admin_alerts_org_id"), ["org_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_admin_alerts_resolved_at"), ["resolved_at"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("admin_alerts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_admin_alerts_resolved_at"))
        batch_op.drop_index(batch_op.f("ix_admin_alerts_org_id"))
    op.drop_table("admin_alerts")
    with op.batch_alter_table("budgets", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_budgets_user_id"))
        batch_op.drop_constraint(batch_op.f("fk_budgets_user_id_users"), type_="foreignkey")
        batch_op.drop_column("user_id")
    with op.batch_alter_table("usage_events", schema=None) as batch_op:
        batch_op.drop_index("ix_usage_events_org_user_created")
        batch_op.drop_constraint(batch_op.f("fk_usage_events_user_id_users"), type_="foreignkey")
        batch_op.drop_column("user_id")
