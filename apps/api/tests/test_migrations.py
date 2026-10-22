"""The Alembic migration chain must build the same schema as the models."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

API_DIR = Path(__file__).resolve().parents[1]
EXPECTED_TABLES = {
    "organizations",
    "users",
    "memberships",
    "invites",
    "api_tokens",
    "refresh_tokens",
    "audit_log",
    "assistants",
    "assistant_versions",
    "conversations",
    "messages",
    "runs",
    "usage_events",
    "data_sources",
    "documents",
    "chunks",
    "alembic_version",
}


_VERSIONS_DIR = API_DIR / "app" / "db" / "migrations" / "versions"


def _alembic(db_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return alembic_at(f"sqlite+aiosqlite:///{db_path}", *args)


def alembic_at(url: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Run alembic against any database (Phase 7b.9: Postgres too)."""
    env = {
        **os.environ,
        "APP_ENV": "test",
        "DATABASE_URL": url,
        "JWT_SECRET": "x" * 40,
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
    )


def _migrated_sqlite(tmp_path: Path) -> Path:
    # pytest's own temp directory: it keeps only the last few runs. A
    # hand-made mkdtemp here left two folders behind on every run.
    db_path = tmp_path / "mig.db"
    result = _alembic(db_path, "upgrade", "head")
    assert result.returncode == 0, result.stderr
    return db_path


@pytest.mark.skipif(
    not list(_VERSIONS_DIR.glob("[0-9]*.py")),
    reason="no migration revisions yet",
)
def test_alembic_upgrade_head_builds_schema(tmp_path: Path) -> None:
    db_path = _migrated_sqlite(tmp_path)

    engine = create_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    assert tables >= EXPECTED_TABLES, f"missing: {EXPECTED_TABLES - tables}"


@pytest.mark.skipif(
    not list(_VERSIONS_DIR.glob("[0-9]*.py")),
    reason="no migration revisions yet",
)
def test_models_and_migrations_agree(tmp_path: Path) -> None:
    """`alembic check`: the migrated schema matches the models exactly (F-7).

    This is what catches a model change shipped without its migration, and
    — the case that bit — an index created by raw SQL but never declared,
    which the next autogenerate would silently drop. The Postgres side of the
    same check, where the pgvector/GIN indexes exist, runs in CI's
    `api-integration` job and in `scripts/check.ps1` (Phase 7b.9)."""
    result = _alembic(_migrated_sqlite(tmp_path), "check")
    assert result.returncode == 0, result.stdout + result.stderr


# ── Phase 7, there and back (7b.9) ───────────────────────────

#: The head before Phase 7's first migration (7a.8, the append-only audit
#: log). Everything after it was written for the internal rollout.
BEFORE_PHASE_7 = "2e9a99664235"


def test_phase_7_migrations_go_there_and_back_with_data(tmp_path: Path) -> None:
    """Seed rows at the head before Phase 7, upgrade, downgrade, upgrade
    again: each data step runs on real rows, and each downgrade undoes its
    schema. The models still match at the end."""
    from tests.test_version_review_migration import _insert

    db_path = tmp_path / "phase7.db"
    assert (r := _alembic(db_path, "upgrade", BEFORE_PHASE_7)).returncode == 0, r.stderr
    import sqlite3
    import uuid

    org, solo, user, other = (uuid.uuid4().hex for _ in range(4))
    assistant, version = uuid.uuid4().hex, uuid.uuid4().hex
    db = sqlite3.connect(db_path)
    _insert(db, "users", id=user, email="owner@example.com")
    _insert(db, "users", id=other, email="member@example.com")
    _insert(db, "organizations", id=org, is_personal=1)
    _insert(db, "organizations", id=solo, is_personal=1)
    _insert(db, "memberships", org_id=org, user_id=user, role="owner")
    _insert(db, "memberships", org_id=org, user_id=other, role="member")
    _insert(db, "memberships", org_id=solo, user_id=other, role="owner")
    _insert(
        db,
        "assistants",
        id=assistant,
        org_id=org,
        created_by=user,
        status="published",
        current_version_id=version,
    )
    _insert(
        db, "assistant_versions", id=version, assistant_id=assistant, org_id=org, version_number=1
    )
    _insert(db, "conversations", org_id=org, assistant_id=assistant, assistant_version_id=version)
    _insert(db, "invites", org_id=org, email="late@example.com", invited_by=user)
    _insert(db, "audit_log", org_id=org, actor_user_id=user, action="org.create")
    db.commit()
    db.close()

    for step in (("upgrade", "head"), ("downgrade", BEFORE_PHASE_7), ("upgrade", "head")):
        result = _alembic(db_path, *step)
        assert result.returncode == 0, f"{step}: {result.stderr}"
    assert (r := _alembic(db_path, "check")).returncode == 0, r.stdout + r.stderr

    db = sqlite3.connect(db_path)
    assert db.execute("SELECT status FROM assistant_versions").fetchone() == ("approved",)
    assert db.execute("SELECT is_personal FROM organizations WHERE id = ?", (org,)).fetchone() == (
        0,
    ), "two people in it: the team's"
    assert db.execute(
        "SELECT actor_label FROM audit_log WHERE action = 'org.create'"
    ).fetchone() == ("owner@example.com",)
    db.close()
