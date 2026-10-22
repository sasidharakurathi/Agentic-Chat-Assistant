"""Audiences and the catalog: groups, listings, Official

Phase 8.5. Groups of people kept in the app; an assistant's draft listing
(category, starters, audience) and, frozen into each version, its listing
as submitted; an admin's Official mark. Every version already there is
given a listing for everyone, as it was: nothing changes for anyone.

Revision ID: c8a5d1e2f3a4
Revises: c8a4c1d2e3f4"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8a5d1e2f3a4"
down_revision: str | None = "c8a4c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine:  # type: ignore[type-arg]
    return sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "groups",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
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
            name=op.f("fk_groups_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_groups_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_groups")),
        sa.UniqueConstraint("org_id", "name", name="uq_groups_org_name"),
    )
    with op.batch_alter_table("groups", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_groups_org_id"), ["org_id"], unique=False)
    op.create_table(
        "group_members",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("added_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["added_by"],
            ["users.id"],
            name=op.f("fk_group_members_added_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name=op.f("fk_group_members_group_id_groups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_group_members_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_group_members_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_group_members")),
        sa.UniqueConstraint("group_id", "user_id", name="uq_group_members_group_user"),
    )
    with op.batch_alter_table("group_members", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_group_members_group_id"), ["group_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_group_members_org_id"), ["org_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_group_members_user_id"), ["user_id"], unique=False)

    with op.batch_alter_table("assistants", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("draft_listing", _json(), nullable=False, server_default=sa.text("'{}'"))
        )
        batch_op.add_column(sa.Column("official_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("official_by", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f("fk_assistants_official_by_users"),
            "users",
            ["official_by"],
            ["id"],
            ondelete="SET NULL",
        )
    with op.batch_alter_table("assistant_versions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("listing", _json(), nullable=True))

    # Every version already there: for everyone, with the name and
    # description the assistant has now.
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT v.id, a.name, a.description FROM assistant_versions v "
            "JOIN assistants a ON a.id = v.assistant_id"
        )
    ).all()
    versions = sa.table(
        "assistant_versions", sa.column("id", sa.Uuid()), sa.column("listing", _json())
    )
    for version_id, name, description in rows:
        listing = {
            "name": name,
            "description": description or "",
            "category": "",
            "starters": [],
            "audience": {"kind": "everyone", "group_ids": [], "user_ids": []},
        }
        # SQLite hands the id back as text; the Uuid column binds a UUID.
        vid = version_id if isinstance(version_id, uuid.UUID) else uuid.UUID(str(version_id))
        bind.execute(sa.update(versions).where(versions.c.id == vid).values(listing=listing))


def downgrade() -> None:
    with op.batch_alter_table("assistant_versions", schema=None) as batch_op:
        batch_op.drop_column("listing")
    with op.batch_alter_table("assistants", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_assistants_official_by_users"), type_="foreignkey")
        batch_op.drop_column("official_by")
        batch_op.drop_column("official_at")
        batch_op.drop_column("draft_listing")
    with op.batch_alter_table("group_members", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_group_members_user_id"))
        batch_op.drop_index(batch_op.f("ix_group_members_org_id"))
        batch_op.drop_index(batch_op.f("ix_group_members_group_id"))
    op.drop_table("group_members")
    with op.batch_alter_table("groups", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_groups_org_id"))
    op.drop_table("groups")
