"""runs.trace_id

The plan (§2.5) specifies `runs.trace_id` so a turn can be found in the
tracing backend (Langfuse / any OTLP collector) and in the logs. It was never
added, so spans and log lines had nothing on the run row to correlate with.

Revision ID: c5a1f7e2d904
Revises: b3d8e5f1a270
Create Date: 2026-09-23 12:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5a1f7e2d904"
down_revision: str | None = "b3d8e5f1a270"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.add_column(sa.Column("trace_id", sa.String(length=32), nullable=True))
        batch.create_index(batch.f("ix_runs_trace_id"), ["trace_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.drop_index(batch.f("ix_runs_trace_id"))
        batch.drop_column("trace_id")
