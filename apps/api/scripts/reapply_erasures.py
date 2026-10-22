"""Erase again what was erased since a backup was taken (Phase 8.10).

A restore brings back conversations the retention rule, or an owner
erasing someone, had already erased for good, and it rolls back the
`erasures` record itself. So `deploy/backup/restore.sh` saves that record
(as JSON lines) before it replaces the database, and prints this, to run
once the API is up again:

    COMPOSE exec -T api python -m scripts.reapply_erasures --load - < erasures-<when>.jsonl

`--load` adds the saved rows the restored database is missing; then every
recorded erasure is applied again, oldest first: the same conversations,
memory notes and feedback comments. Legal holds still apply. Without
`--load` it applies what the database already records. Prints how many
conversations went.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.db.session import get_sessionmaker
from app.services import retention


async def main(rows: list[dict] | None) -> tuple[int, int]:
    added = 0
    async with get_sessionmaker()() as session:
        if rows is not None:
            added = await retention.load_erasures(session, rows)
        return added, await retention.reapply(session)


def _read(load: str) -> list[dict]:
    text = sys.stdin.read() if load == "-" else Path(load).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--load", help="a saved erasures file (JSON lines), or - for stdin")
    args = parser.parse_args()
    added, erased = asyncio.run(main(_read(args.load) if args.load else None))
    print(f"records added: {added}; erased again: {erased} conversations")
