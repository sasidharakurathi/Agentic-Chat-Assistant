"""audit log append-only

Phase 7a.8. The audit log is the durable record of who did what (it is in
the backups and outlives the containers' logs), so on Postgres a trigger
refuses to delete a row or to change one. The only change allowed is the one
the foreign keys make themselves: `ON DELETE SET NULL` when the user or org a
row names is deleted, which blanks that reference and nothing else.

TRUNCATE is not covered: it needs the table owner's rights, which an
attacker with only the app's queries does not get from them.

Revision ID: b7a8c0d1e2f3
Revises: 2e9a99664235"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b7a8c0d1e2f3"
down_revision: str | None = "2e9a99664235"
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
     OR (NEW.org_id IS DISTINCT FROM OLD.org_id AND NEW.org_id IS NOT NULL)
     OR (NEW.actor_user_id IS DISTINCT FROM OLD.actor_user_id AND NEW.actor_user_id IS NOT NULL)
  THEN
    RAISE EXCEPTION 'audit_log is append-only: rows cannot be changed';
  END IF;
  RETURN NEW;
END
$$ LANGUAGE plpgsql;
"""

TRIGGER = """
CREATE TRIGGER audit_log_append_only
BEFORE UPDATE OR DELETE ON audit_log
FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();
"""


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(FUNCTION)
    op.execute(TRIGGER)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP TRIGGER IF EXISTS audit_log_append_only ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_append_only()")
