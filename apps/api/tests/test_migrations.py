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
    "alembic_version",
}


_VERSIONS_DIR = API_DIR / "app" / "db" / "migrations" / "versions"


@pytest.mark.skipif(
    not list(_VERSIONS_DIR.glob("[0-9]*.py")),
    reason="no migration revisions yet",
)
def test_alembic_upgrade_head_builds_schema() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="assistant-studio-mig-"))
    db_path = tmp / "mig.db"
    env = {
        **os.environ,
        "APP_ENV": "test",
        "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
        "JWT_SECRET": "x" * 40,
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    engine = create_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    assert tables >= EXPECTED_TABLES, f"missing: {EXPECTED_TABLES - tables}"
