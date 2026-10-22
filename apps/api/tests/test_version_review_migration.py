"""What the approval gate's migration carries over (Phase 7b.1).

Run against real rows at the revision before it: every version already
published becomes approved, with no reviewer and an audit row per assistant
saying so; a conversation that answered from the draft becomes a preview;
an assistant marked published with nothing published goes back to draft.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from tests.test_migrations import _alembic

BEFORE = "b7a8c0d1e2f3"


def _insert(db: sqlite3.Connection, table: str, **values: Any) -> None:
    """Fill the columns the row must have with a placeholder, then `values`."""
    row: dict[str, Any] = {}
    for _cid, name, type_, notnull, default, pk in db.execute(f"PRAGMA table_info({table})"):
        # `now()` is Postgres's: SQLite would fail on the default.
        if (notnull or pk) and (default is None or "now" in str(default).lower()):
            kind = str(type_).upper()
            row[name] = (
                0
                if any(t in kind for t in ("INT", "NUMERIC", "BOOL"))
                else "{}"
                if "JSON" in kind
                else "2026-01-01 00:00:00"
                if "DATETIME" in kind
                else uuid.uuid4().hex
            )
    row.update(values)
    cols = ", ".join(row)
    db.execute(
        f"INSERT INTO {table} ({cols}) VALUES ({', '.join('?' * len(row))})", list(row.values())
    )


def test_published_versions_are_grandfathered_and_draft_chats_become_previews(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "gate.db"
    result = _alembic(db_path, "upgrade", BEFORE)
    assert result.returncode == 0, result.stderr

    org = uuid.uuid4().hex
    live, draft, fake = uuid.uuid4().hex, uuid.uuid4().hex, uuid.uuid4().hex
    v1, v2 = uuid.uuid4().hex, uuid.uuid4().hex
    db = sqlite3.connect(db_path)
    _insert(db, "organizations", id=org)
    _insert(db, "assistants", id=live, org_id=org, status="published", current_version_id=v2)
    _insert(db, "assistants", id=draft, org_id=org, status="draft")
    # Only the old status edit could do this: published, nothing published.
    _insert(db, "assistants", id=fake, org_id=org, status="published")
    for vid, n in ((v1, 1), (v2, 2)):
        _insert(db, "assistant_versions", id=vid, assistant_id=live, org_id=org, version_number=n)
    on_version = uuid.uuid4().hex
    on_draft = uuid.uuid4().hex
    _insert(
        db, "conversations", id=on_version, assistant_id=live, org_id=org, assistant_version_id=v2
    )
    _insert(db, "conversations", id=on_draft, assistant_id=draft, org_id=org)
    db.commit()
    db.close()

    result = _alembic(db_path, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    db = sqlite3.connect(db_path)
    versions = db.execute(
        "SELECT version_number, status, reviewed_by FROM assistant_versions ORDER BY 1"
    ).fetchall()
    assert versions == [(1, "approved", None), (2, "approved", None)]
    modes = dict(db.execute("SELECT id, mode FROM conversations").fetchall())
    assert modes == {on_version: "live", on_draft: "preview"}
    statuses = dict(db.execute("SELECT id, status FROM assistants").fetchall())
    assert statuses == {live: "published", draft: "draft", fake: "draft"}
    audit = db.execute("SELECT action, target_id, meta FROM audit_log").fetchall()
    assert [(a, uuid.UUID(t).hex, json.loads(m)["versions"]) for a, t, m in audit] == [
        ("assistant.version.grandfathered", live, 2)
    ]
    db.close()

    result = _alembic(db_path, "downgrade", BEFORE)
    assert result.returncode == 0, result.stderr
