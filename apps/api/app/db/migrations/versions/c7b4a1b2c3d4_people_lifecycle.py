"""people lifecycle: deactivation, revocable invites, co-editors

Phase 7b.4.

- A membership can be deactivated (removed from the org) and reactivated;
  the row stays so the person's name stays on what they did.
- An invite can be revoked, and an org has at most one open invite per
  address. Older duplicates still open are revoked first, newest kept.
- Assistants can have co-editors besides their creator.

Revision ID: c7b4a1b2c3d4
Revises: c7b3e1f2a3b4"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7b4a1b2c3d4"
down_revision: str | None = "c7b3e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OPEN = "accepted_at IS NULL AND revoked_at IS NULL"


def upgrade() -> None:
    with op.batch_alter_table("memberships", schema=None) as batch_op:
        batch_op.add_column(sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("deactivated_by", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f("fk_memberships_deactivated_by_users"),
            "users",
            ["deactivated_by"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("invites", schema=None) as batch_op:
        batch_op.add_column(sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("revoked_by", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f("fk_invites_revoked_by_users"),
            "users",
            ["revoked_by"],
            ["id"],
            ondelete="SET NULL",
        )
    # Keep the newest open invite per address; revoke the rest, so the
    # unique index below can be built.
    op.execute(
        "UPDATE invites SET revoked_at = CURRENT_TIMESTAMP "
        "WHERE accepted_at IS NULL AND revoked_at IS NULL AND EXISTS ("
        "SELECT 1 FROM invites newer WHERE newer.org_id = invites.org_id "
        "AND newer.email = invites.email AND newer.accepted_at IS NULL "
        "AND newer.revoked_at IS NULL AND newer.created_at > invites.created_at)"
    )
    op.create_index(
        "uq_invites_one_open",
        "invites",
        ["org_id", "email"],
        unique=True,
        postgresql_where=sa.text(_OPEN),
        sqlite_where=sa.text(_OPEN),
    )

    op.create_table(
        "assistant_editors",
        sa.Column("assistant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("granted_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["assistant_id"],
            ["assistants.id"],
            name=op.f("fk_assistant_editors_assistant_id_assistants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by"],
            ["users.id"],
            name=op.f("fk_assistant_editors_granted_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_assistant_editors_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_assistant_editors_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assistant_editors")),
        sa.UniqueConstraint("assistant_id", "user_id", name="uq_assistant_editors_user"),
    )
    for col in ("assistant_id", "user_id", "org_id"):
        op.create_index(
            op.f(f"ix_assistant_editors_{col}"), "assistant_editors", [col], unique=False
        )


def downgrade() -> None:
    for col in ("assistant_id", "user_id", "org_id"):
        op.drop_index(op.f(f"ix_assistant_editors_{col}"), table_name="assistant_editors")
    op.drop_table("assistant_editors")
    op.drop_index("uq_invites_one_open", table_name="invites")
    with op.batch_alter_table("invites", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_invites_revoked_by_users"), type_="foreignkey")
        batch_op.drop_column("revoked_by")
        batch_op.drop_column("revoked_at")
    with op.batch_alter_table("memberships", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_memberships_deactivated_by_users"), type_="foreignkey"
        )
        batch_op.drop_column("deactivated_by")
        batch_op.drop_column("deactivated_at")
