"""run stop reason and fallback model

Task 5.4. Why a turn's main loop stopped ("end_turn", "refusal", ...) and,
when the fallback model answered instead of the main one, which model that
was. A refused turn is also a new run status, `refused`; the status column
is a plain string, so that needs no change here.

Revision ID: c9331dc49c69
Revises: 21b901a0b512
Create Date: 2026-09-29 07:00:05.246483+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c9331dc49c69"
down_revision: str | None = "21b901a0b512"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("stop_reason", sa.String(length=40), nullable=True))
    op.add_column("runs", sa.Column("fallback_model", sa.String(length=80), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "fallback_model")
    op.drop_column("runs", "stop_reason")
