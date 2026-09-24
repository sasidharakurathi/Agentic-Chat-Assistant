"""data_sources.ingest_report

What the last ingestion of a source did (chunks, embeddings reused from the
previous index, context coverage, why context was skipped, cost), so the
sources page can say more than "ready" (tasks 2.5 and 2.6).

Revision ID: a5c7e9f1b358
Revises: f4a6b8c0d236
Create Date: 2026-09-24 09:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a5c7e9f1b358"
down_revision: str | None = "f4a6b8c0d236"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")
    op.add_column("data_sources", sa.Column("ingest_report", json_type, nullable=True))


def downgrade() -> None:
    op.drop_column("data_sources", "ingest_report")
