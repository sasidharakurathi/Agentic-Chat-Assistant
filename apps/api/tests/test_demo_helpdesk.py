"""The demo helpdesk database for the IT helpdesk sample (Phase 9.8): the
file it writes, and the actions it prints, are ones the platform accepts."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.schemas.assistant_config import DeclaredAction
from scripts import demo_helpdesk


def test_it_builds_tickets_for_each_person(tmp_path: Path) -> None:
    path = tmp_path / "helpdesk.sqlite"
    demo_helpdesk.build(path, ["pat@example.com", "sam@example.com"])
    db = sqlite3.connect(path)
    try:
        rows = db.execute(
            "SELECT requester_email, count(*) FROM tickets GROUP BY requester_email"
        ).fetchall()
        assert dict(rows) == {"pat@example.com": 3, "sam@example.com": 3}
        assert db.execute("SELECT count(*) FROM access_requests").fetchone() == (0,)
    finally:
        db.close()
    # Re-running rebuilds rather than doubling.
    demo_helpdesk.build(path, ["pat@example.com"])
    db = sqlite3.connect(path)
    try:
        assert db.execute("SELECT count(*) FROM tickets").fetchone() == (3,)
    finally:
        db.close()


def test_its_actions_pass_the_checks_a_builder_would_meet() -> None:
    actions = [DeclaredAction.model_validate(a) for a in demo_helpdesk.ACTIONS]
    assert [(a.name, a.tier) for a in actions] == [
        ("my_tickets", "read"),
        ("request_access", "confirm"),
        ("close_ticket", "approver"),
    ]


def test_it_goes_where_production_can_open_it(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DB_SQLITE_DIR", str(tmp_path))
    assert demo_helpdesk.default_out() == tmp_path / "helpdesk.sqlite"
