"""db connection uri secret

A sealed connection string for MongoDB connections. Before this the only
place for one was `db_connections.options["uri"]` — plaintext JSONB, returned
verbatim by every endpoint. Connections now point at a `secrets` row instead,
exactly as passwords do.

No data migration: no row has ever stored `options.uri` outside of tests, and
the API now refuses to accept one.

Revision ID: 9e41c7a2d5b8
Revises: 72f2c204f174
Create Date: 2026-09-23 00:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9e41c7a2d5b8"
down_revision: str | None = "72f2c204f174"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # batch mode so the FK can be added on SQLite (the unit tier) as well as
    # Postgres, which cannot ALTER TABLE ... ADD CONSTRAINT in SQLite.
    with op.batch_alter_table("db_connections") as batch:
        batch.add_column(sa.Column("uri_secret_ref", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_db_connections_uri_secret_ref_secrets",
            "secrets",
            ["uri_secret_ref"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("db_connections") as batch:
        batch.drop_constraint("fk_db_connections_uri_secret_ref_secrets", type_="foreignkey")
        batch.drop_column("uri_secret_ref")
