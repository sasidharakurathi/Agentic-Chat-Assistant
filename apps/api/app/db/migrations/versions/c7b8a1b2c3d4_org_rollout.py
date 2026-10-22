"""organisations: archived, settings, and the personal orgs left behind

Phase 7b.8. Organisations can be archived (nobody reaches them, nothing is
deleted) and carry the owner's settings. Then the personal orgs made at
sign-up before 7b.4 are sorted out:

- a personal org that other people have joined is the team's organisation,
  so it stops being personal (on a server that grew from one person's
  sign-up, it is the company org);
- a personal org with nothing in it (no assistants, conversations, sources
  or spend) is archived, when each of its members has another organisation
  to land in;
- every other personal org stays as it is, for the operator to decide
  (`python -m scripts.upgrade_report`).

Each change has an audit row in the org it touched.

Revision ID: c7b8a1b2c3d4
Revises: c7b7a2b3c4d5"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "c7b8a1b2c3d4"
down_revision: str | None = "c7b7a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Why the upgrade archived an org, as the operator's report shows it.
ARCHIVED_EMPTY = "Empty personal organisation, archived by the upgrade (7b.8)"

orgs = sa.table(
    "organizations",
    sa.column("id", sa.Uuid()),
    sa.column("is_personal", sa.Boolean()),
    sa.column("archived_at", sa.DateTime(timezone=True)),
    sa.column("archived_reason", sa.String()),
)
members = sa.table(
    "memberships",
    sa.column("org_id", sa.Uuid()),
    sa.column("user_id", sa.Uuid()),
    sa.column("deactivated_at", sa.DateTime(timezone=True)),
)
audit = sa.table(
    "audit_log",
    sa.column("id", sa.Uuid()),
    sa.column("org_id", sa.Uuid()),
    sa.column("action", sa.String()),
    sa.column("target_type", sa.String()),
    sa.column("target_id", sa.String()),
    sa.column("meta", sa.JSON()),
)
#: What counts as something in an org.
CONTENT = ("assistants", "conversations", "data_sources", "usage_events")


def _has_content(bind: sa.Connection, org_id: uuid.UUID) -> bool:
    for name in CONTENT:
        t = sa.table(name, sa.column("org_id", sa.Uuid()))
        if bind.execute(sa.select(sa.literal(1)).where(t.c.org_id == org_id).limit(1)).first():
            return True
    return False


def _lands_elsewhere(bind: sa.Connection, org_id: uuid.UUID) -> bool:
    """Each member of `org_id` is active in a team org that isn't archived."""
    users = bind.execute(sa.select(members.c.user_id).where(members.c.org_id == org_id)).scalars()
    for user_id in users:
        other = bind.execute(
            sa.select(sa.literal(1))
            .select_from(members.join(orgs, orgs.c.id == members.c.org_id))
            .where(
                members.c.user_id == user_id,
                members.c.org_id != org_id,
                members.c.deactivated_at.is_(None),
                orgs.c.is_personal.is_(False),
                orgs.c.archived_at.is_(None),
            )
            .limit(1)
        ).first()
        if other is None:
            return False
    return True


def _record(org_id: uuid.UUID, action: str, meta: dict[str, str | int]) -> dict[str, object]:
    return {
        "id": uuid.uuid4(),
        "org_id": org_id,
        "action": action,
        "target_type": "org",
        "target_id": str(org_id),
        "meta": meta,
    }


def upgrade() -> None:
    with op.batch_alter_table("organizations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("archived_reason", sa.String(length=200), nullable=True))
        batch_op.add_column(
            sa.Column("settings", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
        )

    bind = op.get_bind()
    rows: list[dict[str, object]] = []
    personal = [
        uuid.UUID(str(i))
        for i in bind.execute(sa.select(orgs.c.id).where(orgs.c.is_personal.is_(True))).scalars()
    ]
    # First the shared ones, so a person's landing org counts below.
    alone = []
    for org_id in personal:
        count = bind.execute(
            sa.select(sa.func.count()).select_from(members).where(members.c.org_id == org_id)
        ).scalar_one()
        if count > 1:
            bind.execute(sa.update(orgs).where(orgs.c.id == org_id).values(is_personal=False))
            rows.append(_record(org_id, "org.became_team", {"members": int(count)}))
        else:
            alone.append(org_id)
    now = datetime.now(UTC)
    for org_id in alone:
        if _has_content(bind, org_id) or not _lands_elsewhere(bind, org_id):
            continue
        bind.execute(
            sa.update(orgs)
            .where(orgs.c.id == org_id)
            .values(archived_at=now, archived_reason=ARCHIVED_EMPTY)
        )
        rows.append(_record(org_id, "org.archive", {"reason": ARCHIVED_EMPTY}))
    if rows:
        op.bulk_insert(audit, rows)


def downgrade() -> None:
    # A personal org that became the team's stays a team org: which ones
    # were personal is in the audit log (`org.became_team`).
    with op.batch_alter_table("organizations", schema=None) as batch_op:
        batch_op.drop_column("settings")
        batch_op.drop_column("archived_reason")
        batch_op.drop_column("archived_at")
