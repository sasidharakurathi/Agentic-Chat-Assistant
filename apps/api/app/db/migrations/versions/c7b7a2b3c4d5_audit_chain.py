"""audit log: a hash chain, and who acted as they were then

Phase 7b.7. Each audit row gets, once, a sequence number and a hash over its
contents and the previous row's hash (`services.audit_chain` seals them in
order). The append-only trigger (7a.8) now allows exactly that one write on
a row: setting the seal on a row that has none. Every other change is still
refused. `actor_label` keeps the actor's email at the time.

Existing rows get their label from the users still there, and are sealed by
the first run of the sealer.

Revision ID: c7b7a2b3c4d5
Revises: c7b6f1a2b3c4"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7b7a2b3c4d5"
down_revision: str | None = "c7b6f1a2b3c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FUNCTION = """
CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'audit_log is append-only: rows cannot be deleted';
  END IF;
  IF NEW.id IS DISTINCT FROM OLD.id
     OR NEW.action IS DISTINCT FROM OLD.action
     OR NEW.target_type IS DISTINCT FROM OLD.target_type
     OR NEW.target_id IS DISTINCT FROM OLD.target_id
     OR NEW.meta::text IS DISTINCT FROM OLD.meta::text
     OR NEW.ip IS DISTINCT FROM OLD.ip
     OR NEW.created_at IS DISTINCT FROM OLD.created_at
     OR (NEW.actor_label IS DISTINCT FROM OLD.actor_label AND OLD.seq IS NOT NULL)
     OR (NEW.org_id IS DISTINCT FROM OLD.org_id AND NEW.org_id IS NOT NULL)
     OR (NEW.actor_user_id IS DISTINCT FROM OLD.actor_user_id AND NEW.actor_user_id IS NOT NULL)
     OR (OLD.seq IS NOT NULL AND (
          NEW.seq IS DISTINCT FROM OLD.seq
          OR NEW.prev_hash IS DISTINCT FROM OLD.prev_hash
          OR NEW.row_hash IS DISTINCT FROM OLD.row_hash))
  THEN
    RAISE EXCEPTION 'audit_log is append-only: rows cannot be changed';
  END IF;
  RETURN NEW;
END
$$ LANGUAGE plpgsql;
"""

PREVIOUS = """
CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'audit_log is append-only: rows cannot be deleted';
  END IF;
  IF NEW.id IS DISTINCT FROM OLD.id
     OR NEW.action IS DISTINCT FROM OLD.action
     OR NEW.target_type IS DISTINCT FROM OLD.target_type
     OR NEW.target_id IS DISTINCT FROM OLD.target_id
     OR NEW.meta::text IS DISTINCT FROM OLD.meta::text
     OR NEW.ip IS DISTINCT FROM OLD.ip
     OR NEW.created_at IS DISTINCT FROM OLD.created_at
     OR (NEW.org_id IS DISTINCT FROM OLD.org_id AND NEW.org_id IS NOT NULL)
     OR (NEW.actor_user_id IS DISTINCT FROM OLD.actor_user_id AND NEW.actor_user_id IS NOT NULL)
  THEN
    RAISE EXCEPTION 'audit_log is append-only: rows cannot be changed';
  END IF;
  RETURN NEW;
END
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    with op.batch_alter_table("audit_log", schema=None) as batch_op:
        batch_op.add_column(sa.Column("actor_label", sa.String(length=320), nullable=True))
        batch_op.add_column(sa.Column("seq", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("prev_hash", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("row_hash", sa.String(length=64), nullable=True))
        batch_op.create_unique_constraint(batch_op.f("uq_audit_log_seq"), ["seq"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute(FUNCTION)
    op.execute(
        "UPDATE audit_log SET actor_label = ("
        "SELECT email FROM users WHERE users.id = audit_log.actor_user_id) "
        "WHERE actor_user_id IS NOT NULL"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(PREVIOUS)
    with op.batch_alter_table("audit_log", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("uq_audit_log_seq"), type_="unique")
        batch_op.drop_column("row_hash")
        batch_op.drop_column("prev_hash")
        batch_op.drop_column("seq")
        batch_op.drop_column("actor_label")
