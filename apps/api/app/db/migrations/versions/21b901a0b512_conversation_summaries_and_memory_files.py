"""conversation summaries and memory files

Task 5.2. `conversations` gets a rolling summary of its older messages and
two version counters: the summary's, and the one the current SDK session was
started from, so a turn knows when to start fresh from the summary instead
of resuming (see `agent/history.py`). `memory_files` is the memory tool's
store, scoped per assistant and per person (`owner_key`).

Revision ID: 21b901a0b512
Revises: f06d6e8244fd
Create Date: 2026-09-29 05:34:10.593822+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "21b901a0b512"
down_revision: str | None = "f06d6e8244fd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memory_files",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("assistant_id", sa.Uuid(), nullable=False),
        sa.Column("owner_key", sa.String(length=220), nullable=False),
        sa.Column("path", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["assistant_id"],
            ["assistants.id"],
            name=op.f("fk_memory_files_assistant_id_assistants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_memory_files_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_files")),
        sa.UniqueConstraint("assistant_id", "owner_key", "path", name="uq_memory_file_path"),
    )
    op.create_index(op.f("ix_memory_files_org_id"), "memory_files", ["org_id"], unique=False)
    op.create_index(
        "ix_memory_files_owner", "memory_files", ["assistant_id", "owner_key"], unique=False
    )
    op.add_column("conversations", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column(
        "conversations", sa.Column("summary_through_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "conversations",
        sa.Column("summary_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "conversations",
        sa.Column("session_summary_version", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("conversations", "session_summary_version")
    op.drop_column("conversations", "summary_version")
    op.drop_column("conversations", "summary_through_at")
    op.drop_column("conversations", "summary")
    op.drop_index("ix_memory_files_owner", table_name="memory_files")
    op.drop_index(op.f("ix_memory_files_org_id"), table_name="memory_files")
    op.drop_table("memory_files")
