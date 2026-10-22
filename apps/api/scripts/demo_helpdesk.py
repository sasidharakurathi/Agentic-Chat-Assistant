"""The demo helpdesk database for the IT helpdesk sample (Phase 9.8).

A small SQLite file with tickets, access requests and laptops for a few
people, so the sample's database actions can be tried end to end:

- **My tickets** (read): `SELECT id, title, status, priority FROM tickets
  WHERE requester_email = :asker_email`
- **Request access to an app** (the person confirms): an INSERT pinned to
  `:asker_email`
- **Close a ticket** (an approver decides): an UPDATE by id

    python -m scripts.demo_helpdesk                  (from apps/api)
    python -m scripts.demo_helpdesk --people pat@acme.example,sam@acme.example
    python -m scripts.demo_helpdesk --out D:/data/helpdesk.sqlite

It is written to DB_SQLITE_DIR when that is set (production only opens
SQLite files there), else to testdata/manual/helpdesk.sqlite. Safe to
re-run: the tables are dropped and rebuilt. It then prints what to paste:
the connection to add in the assistant's Databases tab, and the three
actions for the database's drawer.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

SCHEMA = """
DROP TABLE IF EXISTS tickets;
DROP TABLE IF EXISTS access_requests;
DROP TABLE IF EXISTS laptops;
CREATE TABLE tickets (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    priority TEXT NOT NULL DEFAULT 'P3',
    requester_email TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE access_requests (
    id INTEGER PRIMARY KEY,
    app TEXT NOT NULL,
    reason TEXT,
    requester_email TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'waiting for manager',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE laptops (
    asset_tag TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    assigned_email TEXT
);
"""

TICKETS = [
    ("VPN drops every hour", "open", "P2"),
    ("Laptop fan very loud", "in progress", "P3"),
    ("Need a second monitor", "closed", "P3"),
]

ACTIONS = [
    {
        "name": "my_tickets",
        "title": "Look up my tickets",
        "description": "When someone asks about their own tickets.",
        "sql": "SELECT id, title, status, priority FROM tickets "
        "WHERE requester_email = :asker_email ORDER BY id DESC",
        "tier": "read",
    },
    {
        "name": "request_access",
        "title": "Request access to an app",
        "description": "When someone asks for licensed software.",
        "sql": "INSERT INTO access_requests (app, reason, requester_email) "
        "VALUES (:app, :reason, :asker_email)",
        "params": [
            {
                "name": "app",
                "choices": ["Figma", "Slack Enterprise", "Jira", "Adobe Acrobat Pro"],
                "description": "The app",
            },
            {"name": "reason", "required": False, "description": "Why they need it"},
        ],
        "tier": "confirm",
    },
    {
        "name": "close_ticket",
        "title": "Close a ticket",
        "description": "When a problem is fixed and the ticket should be closed.",
        "sql": "UPDATE tickets SET status = 'closed' WHERE id = :id",
        "params": [{"name": "id", "type": "integer", "description": "The ticket number"}],
        "tier": "approver",
    },
]


def default_out() -> Path:
    root = os.environ.get("DB_SQLITE_DIR", "").strip()
    if root:
        return Path(root) / "helpdesk.sqlite"
    return ROOT / "testdata" / "manual" / "helpdesk.sqlite"


def build(path: Path, people: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    try:
        db.executescript(SCHEMA)
        for i, email in enumerate(people):
            for title, status, priority in TICKETS:
                db.execute(
                    "INSERT INTO tickets (title, status, priority, requester_email) "
                    "VALUES (?, ?, ?, ?)",
                    (title, status, priority, email),
                )
            db.execute(
                "INSERT INTO laptops (asset_tag, model, assigned_email) VALUES (?, ?, ?)",
                (f"ACME-{1000 + i}", "14-inch standard", email),
            )
        db.commit()
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--people",
        default="pat@example.com,sam@example.com",
        help="Comma-separated emails that get demo tickets (use your test accounts).",
    )
    args = parser.parse_args()
    out = (args.out or default_out()).resolve()
    people = [p.strip().lower() for p in args.people.split(",") if p.strip()]
    build(out, people)
    print(f"Wrote {out} with tickets for {', '.join(people)}.\n")
    print("1. In the assistant's Databases tab, add a SQLite connection to this file,")
    print("   and allow writes (the access request and closing a ticket change data).")
    print("2. Add it to the canvas, open its drawer and paste these actions")
    print("   (or add them one by one under Actions):\n")
    print(json.dumps(ACTIONS, indent=2))


if __name__ == "__main__":
    main()
