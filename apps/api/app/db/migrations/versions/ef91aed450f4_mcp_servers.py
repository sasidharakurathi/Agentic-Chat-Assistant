"""mcp servers

MCP servers registered on an assistant (task 4.3): how to reach one (a
command for stdio, a URL for http/sse), sealed headers and environment
(values in `secrets`, names here), and what it offered when last asked.
Which tools an assistant version uses lives in its config, not here.

Revision ID: ef91aed450f4
Revises: b6d8f0a2c469
Create Date: 2026-09-28 10:20:24.941734+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "ef91aed450f4"
down_revision: str | None = "b6d8f0a2c469"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mcp_servers",
        sa.Column("assistant_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column(
            "transport",
            sa.Enum("stdio", "http", "sse", name="mcp_transport", native_enum=False, length=10),
            nullable=False,
        ),
        sa.Column("command", sa.String(length=500), nullable=True),
        sa.Column(
            "args",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("url", sa.String(length=2048), nullable=True),
        sa.Column("headers_secret_ref", sa.Uuid(), nullable=True),
        sa.Column(
            "header_names",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("env_secret_ref", sa.Uuid(), nullable=True),
        sa.Column(
            "env_keys",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "tools",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("tools_discovered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "sandbox",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "unknown", "ok", "error", name="mcp_server_status", native_enum=False, length=20
            ),
            nullable=False,
        ),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
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
            name=op.f("fk_mcp_servers_assistant_id_assistants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["env_secret_ref"],
            ["secrets.id"],
            name=op.f("fk_mcp_servers_env_secret_ref_secrets"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["headers_secret_ref"],
            ["secrets.id"],
            name=op.f("fk_mcp_servers_headers_secret_ref_secrets"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_mcp_servers_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mcp_servers")),
        sa.UniqueConstraint("assistant_id", "name", name="uq_mcp_servers_assistant_name"),
    )
    op.create_index(
        op.f("ix_mcp_servers_assistant_id"), "mcp_servers", ["assistant_id"], unique=False
    )
    op.create_index(op.f("ix_mcp_servers_org_id"), "mcp_servers", ["org_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_mcp_servers_org_id"), table_name="mcp_servers")
    op.drop_index(op.f("ix_mcp_servers_assistant_id"), table_name="mcp_servers")
    op.drop_table("mcp_servers")
