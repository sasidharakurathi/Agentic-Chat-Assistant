"""The Alembic migration chain must build the same schema as the models."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
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
    env = {
        **os.environ,
        "APP_ENV": "test",
        "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
        "JWT_SECRET": "x" * 40,
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
    )


def _migrated_sqlite() -> Path:
    db_path = Path(tempfile.mkdtemp(prefix="assistant-studio-mig-")) / "mig.db"
    result = _alembic(db_path, "upgrade", "head")
    assert result.returncode == 0, result.stderr
    return db_path


@pytest.mark.skipif(
    not list(_VERSIONS_DIR.glob("[0-9]*.py")),
    reason="no migration revisions yet",
)
def test_alembic_upgrade_head_builds_schema() -> None:
    db_path = _migrated_sqlite()

    engine = create_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    assert tables >= EXPECTED_TABLES, f"missing: {EXPECTED_TABLES - tables}"


@pytest.mark.skipif(
    not list(_VERSIONS_DIR.glob("[0-9]*.py")),
    reason="no migration revisions yet",
)
def test_models_and_migrations_agree() -> None:
    """`alembic check`: the migrated schema matches the models exactly (F-7).

    This is what catches a model change shipped without its migration, and
    — the case that bit — an index created by raw SQL but never declared,
    which the next autogenerate would silently drop. The Postgres side of the
    same check runs in `scripts/check.ps1` against the dev database, since
    the pgvector/GIN indexes only exist there."""
    result = _alembic(_migrated_sqlite(), "check")
    assert result.returncode == 0, result.stdout + result.stderr
