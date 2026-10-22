"""HTTP connections

Phase 9.3. A company system an assistant calls over HTTP: its base URL,
the names of the headers it sends (the values sealed in `secrets`), and
whether it is told who is asking.

Revision ID: c9a3b4c5d6e7
Revises: c9a2b3c4d5e6"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9a3b4c5d6e7"
down_revision: str | None = "c9a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine:  # type: ignore[type-arg]
    return sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "http_connections",
        sa.Column("assistant_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=False),
        sa.Column("headers_secret_ref", sa.Uuid(), nullable=True),
        sa.Column("header_names", _json(), nullable=False),
        sa.Column("send_asker", sa.Boolean(), nullable=False),
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
            name=op.f("fk_http_connections_assistant_id_assistants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["headers_secret_ref"],
            ["secrets.id"],
            name=op.f("fk_http_connections_headers_secret_ref_secrets"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_http_connections_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_http_connections")),
        sa.UniqueConstraint("assistant_id", "name", name="uq_http_connections_assistant_name"),
    )
    with op.batch_alter_table("http_connections", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_http_connections_assistant_id"), ["assistant_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_http_connections_org_id"), ["org_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("http_connections", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_http_connections_org_id"))
        batch_op.drop_index(batch_op.f("ix_http_connections_assistant_id"))
    op.drop_table("http_connections")
