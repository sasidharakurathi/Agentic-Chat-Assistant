"""version review and conversation mode

Phase 7b.1, the approval gate. A version now has a review state: a builder
submits it, an admin approves or rejects it, and only an approved version
answers in Chat. A conversation is either live (Chat, fixed to the approved
version it started on) or a preview (a builder testing the draft, or an
admin the submitted version).

Carried over:

- Every existing version had been published, which made it live at once,
  so each becomes `approved`, with no reviewer. One audit row per assistant
  says so: nobody reviewed them, they were grandfathered in.
- A conversation with no version had answered from the draft: a preview.
- An assistant marked published with nothing published (only an edit could
  do that) goes back to draft.

Revision ID: c7b1a2d3e4f5
Revises: b7a8c0d1e2f3"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7b1a2d3e4f5"
down_revision: str | None = "b7a8c0d1e2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ONE_SUBMITTED = "status = 'submitted'"


def upgrade() -> None:
    # Added with a default so the rows already there are carried over in the
    # same statement, then the default goes: a version must say its state.
    with op.batch_alter_table("assistant_versions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("status", sa.String(length=20), nullable=False, server_default="approved")
        )
        batch_op.add_column(sa.Column("reviewed_by", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(
            sa.Column("review_note", sa.String(length=1000), nullable=False, server_default="")
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_assistant_versions_reviewed_by_users"),
            "users",
            ["reviewed_by"],
            ["id"],
            ondelete="SET NULL",
        )
    with op.batch_alter_table("assistant_versions", schema=None) as batch_op:
        batch_op.alter_column("status", server_default=None)
        batch_op.alter_column("review_note", server_default=None)
    op.create_index(
        "uq_assistant_versions_one_submitted",
        "assistant_versions",
        ["assistant_id"],
        unique=True,
        postgresql_where=sa.text(_ONE_SUBMITTED),
        sqlite_where=sa.text(_ONE_SUBMITTED),
    )

    with op.batch_alter_table("conversations", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("mode", sa.String(length=20), nullable=False, server_default="live")
        )
    op.execute(
        "UPDATE conversations SET mode = 'preview' "
        "WHERE assistant_version_id IS NULL AND eval_run_id IS NULL"
    )
    op.execute(
        "UPDATE assistants SET status = 'draft' "
        "WHERE status = 'published' AND current_version_id IS NULL"
    )

    bind = op.get_bind()
    grandfathered = bind.execute(
        sa.text(
            "SELECT assistant_id, org_id, COUNT(*) FROM assistant_versions "
            "GROUP BY assistant_id, org_id"
        )
    ).all()
    if grandfathered:
        audit = sa.table(
            "audit_log",
            sa.column("id", sa.Uuid()),
            sa.column("org_id", sa.Uuid()),
            sa.column("action", sa.String()),
            sa.column("target_type", sa.String()),
            sa.column("target_id", sa.String()),
            sa.column("meta", sa.JSON()),
        )
        op.bulk_insert(
            audit,
            [
                {
                    "id": uuid.uuid4(),
                    "org_id": uuid.UUID(str(org_id)),
                    "action": "assistant.version.grandfathered",
                    "target_type": "assistant",
                    "target_id": str(uuid.UUID(str(assistant_id))),
                    "meta": {
                        "versions": int(count),
                        "note": "Published before the approval gate; approved without review.",
                    },
                }
                for assistant_id, org_id, count in grandfathered
            ],
        )


def downgrade() -> None:
    with op.batch_alter_table("conversations", schema=None) as batch_op:
        batch_op.drop_column("mode")
    op.drop_index("uq_assistant_versions_one_submitted", table_name="assistant_versions")
    with op.batch_alter_table("assistant_versions", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_assistant_versions_reviewed_by_users"), type_="foreignkey"
        )
        batch_op.drop_column("review_note")
        batch_op.drop_column("reviewed_at")
        batch_op.drop_column("reviewed_by")
        batch_op.drop_column("status")
