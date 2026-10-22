"""version manifest and kept documents

Phase 7b.1 (plan D16): an approved version is frozen. Each version records
a manifest when it is submitted (its documents, and a fingerprint of each
database connection and MCP server and tool it uses); each run records the
manifest it answered with; and a document replaced by a reindex is kept,
marked superseded, while a version still uses it.

Versions from before this have no manifest: the first turn that uses one
records it then.

Revision ID: c7b1d4e5f6a7
Revises: c7b1a2d3e4f5"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7b1d4e5f6a7"
down_revision: str | None = "c7b1a2d3e4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")
    with op.batch_alter_table("assistant_versions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("manifest", json_type, nullable=True))
        batch_op.add_column(sa.Column("manifest_hash", sa.String(length=64), nullable=True))
    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.add_column(sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("manifest_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("runs", schema=None) as batch_op:
        batch_op.drop_column("manifest_hash")
    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.drop_column("superseded_at")
    with op.batch_alter_table("assistant_versions", schema=None) as batch_op:
        batch_op.drop_column("manifest_hash")
        batch_op.drop_column("manifest")
