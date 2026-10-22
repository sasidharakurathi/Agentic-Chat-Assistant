"""Requests that wait safely

Phase 9.1 (plan D17). `approvals` becomes the request itself: the exact
input sealed under the KEK with its hash, the assistant and version that
asked, who decides (`tier`), the decider's note, and what running it did.
`conversation_id` is SET NULL, so removing a conversation keeps the record
of a write. `assistants.action_approvers` names who decides an assistant's
requests, and `approval_delegations` hands an approver's requests to
someone until a date.

Rows from before: an `approved` one ran inside its turn (Phase 7a.7 marked
the ones that didn't as `cancelled`), so it becomes `succeeded`; a
`pending` one belonged to a wait that no longer exists, so it is `expired`.

Revision ID: c9a1b2c3d4e5
Revises: c8a9b4c5d6e7"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9a1b2c3d4e5"
down_revision: str | None = "c8a9b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine:  # type: ignore[type-arg]
    return sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.add_column(sa.Column("assistant_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("assistant_version_id", sa.Uuid(), nullable=True))
        batch_op.add_column(
            sa.Column("preview", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(sa.Column("action_name", sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column("title", sa.String(length=200), nullable=True))
        batch_op.add_column(
            sa.Column("tier", sa.String(length=10), nullable=False, server_default="approver")
        )
        batch_op.add_column(sa.Column("sealed_ciphertext", sa.LargeBinary(), nullable=True))
        batch_op.add_column(sa.Column("sealed_dek", sa.LargeBinary(), nullable=True))
        batch_op.add_column(sa.Column("sealed_nonce", sa.LargeBinary(), nullable=True))
        batch_op.add_column(sa.Column("input_sha256", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("note", sa.String(length=1000), nullable=True))
        batch_op.add_column(sa.Column("reminded_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("result", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.alter_column("conversation_id", existing_type=sa.Uuid(), nullable=True)
        batch_op.drop_constraint(
            batch_op.f("fk_approvals_conversation_id_conversations"), type_="foreignkey"
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_approvals_conversation_id_conversations"),
            "conversations",
            ["conversation_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_approvals_assistant_id_assistants"),
            "assistants",
            ["assistant_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_approvals_assistant_version_id_assistant_versions"),
            "assistant_versions",
            ["assistant_version_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(batch_op.f("ix_approvals_assistant_id"), ["assistant_id"])
        batch_op.create_index("ix_approvals_status_expires", ["status", "expires_at"])

    # Fill the new links in from each row's conversation.
    op.execute(
        "UPDATE approvals SET "
        "assistant_id = (SELECT c.assistant_id FROM conversations c "
        "WHERE c.id = approvals.conversation_id), "
        "assistant_version_id = (SELECT c.assistant_version_id FROM conversations c "
        "WHERE c.id = approvals.conversation_id), "
        "preview = COALESCE((SELECT c.mode = 'preview' FROM conversations c "
        "WHERE c.id = approvals.conversation_id), false)"
    )
    op.execute(
        "UPDATE approvals SET status = 'succeeded', "
        "result = 'Ran in its conversation (before requests waited on their own).' "
        "WHERE status = 'approved'"
    )
    op.execute(
        "UPDATE approvals SET status = 'expired', "
        "decided_at = COALESCE(decided_at, expires_at) WHERE status = 'pending'"
    )

    with op.batch_alter_table("assistants", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("action_approvers", _json(), nullable=False, server_default=sa.text("'{}'"))
        )

    op.create_table(
        "approval_delegations",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("delegate_id", sa.Uuid(), nullable=False),
        sa.Column("until", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["delegate_id"],
            ["users.id"],
            name=op.f("fk_approval_delegations_delegate_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_approval_delegations_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_approval_delegations_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_delegations")),
    )
    with op.batch_alter_table("approval_delegations", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_approval_delegations_delegate_id"), ["delegate_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_approval_delegations_org_id"), ["org_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_approval_delegations_user_id"), ["user_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("approval_delegations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_approval_delegations_user_id"))
        batch_op.drop_index(batch_op.f("ix_approval_delegations_org_id"))
        batch_op.drop_index(batch_op.f("ix_approval_delegations_delegate_id"))
    op.drop_table("approval_delegations")

    with op.batch_alter_table("assistants", schema=None) as batch_op:
        batch_op.drop_column("action_approvers")

    # The states that didn't exist before: a request that ran is what
    # `approved` meant then; one waiting or running goes the way an
    # unanswered wait did; a failed one ran nothing.
    op.execute("UPDATE approvals SET status = 'approved' WHERE status = 'succeeded'")
    op.execute("UPDATE approvals SET status = 'expired' WHERE status IN ('approved', 'executing')")
    op.execute("UPDATE approvals SET status = 'cancelled' WHERE status = 'failed'")
    # Rows whose conversation is gone can't satisfy the old NOT NULL.
    op.execute("DELETE FROM approvals WHERE conversation_id IS NULL")

    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.drop_index("ix_approvals_status_expires")
        batch_op.drop_index(batch_op.f("ix_approvals_assistant_id"))
        batch_op.drop_constraint(
            batch_op.f("fk_approvals_assistant_version_id_assistant_versions"),
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            batch_op.f("fk_approvals_assistant_id_assistants"), type_="foreignkey"
        )
        batch_op.drop_constraint(
            batch_op.f("fk_approvals_conversation_id_conversations"), type_="foreignkey"
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_approvals_conversation_id_conversations"),
            "conversations",
            ["conversation_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.alter_column("conversation_id", existing_type=sa.Uuid(), nullable=False)
        for column in (
            "reported_at",
            "result",
            "executed_at",
            "reminded_at",
            "note",
            "input_sha256",
            "sealed_nonce",
            "sealed_dek",
            "sealed_ciphertext",
            "tier",
            "title",
            "action_name",
            "preview",
            "assistant_version_id",
            "assistant_id",
        ):
            batch_op.drop_column(column)
