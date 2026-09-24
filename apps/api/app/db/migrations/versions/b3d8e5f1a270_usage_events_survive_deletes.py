"""usage events survive deletes

`usage_events` is an append-only ledger of spend that genuinely happened, but
its `assistant_id` and `conversation_id` foreign keys were ON DELETE CASCADE.
Harmless while nothing could delete an assistant; the moment one could, every
deletion would have erased that assistant's spend from the org's billing
totals. Both become SET NULL (the columns were already nullable).

Revision ID: b3d8e5f1a270
Revises: 9e41c7a2d5b8
Create Date: 2026-09-23 00:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b3d8e5f1a270"
down_revision: str | None = "9e41c7a2d5b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FKS = (
    ("fk_usage_events_assistant_id_assistants", "assistants", "assistant_id"),
    ("fk_usage_events_conversation_id_conversations", "conversations", "conversation_id"),
)


def _repoint(ondelete: str) -> None:
    # batch mode: SQLite (the unit tier) cannot alter a constraint in place.
    with op.batch_alter_table("usage_events") as batch:
        for name, parent, column in _FKS:
            batch.drop_constraint(name, type_="foreignkey")
            batch.create_foreign_key(name, parent, [column], ["id"], ondelete=ondelete)


def upgrade() -> None:
    _repoint("SET NULL")


def downgrade() -> None:
    _repoint("CASCADE")
