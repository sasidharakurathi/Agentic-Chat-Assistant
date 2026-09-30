"""Where test runs write their files, and how they are cleaned up.

Every run gets one folder, `%TEMP%/assistant-studio-tests/run-XXXX`
(`new_run_dir`), and `conftest.py` points the process's temp directory at it:
the SQLite test database, pytest's `tmp_path` folders, the migration checks'
databases, the agent's scratch folders and whatever subprocesses write (the
MCP runner's session folders, alembic) all land inside.

A run removes its folder at exit. On Windows one file usually survives
that: the SQLite database, still held open by the app's connection pool
while Python shuts down. So `sweep()` removes finished runs' folders later:
at the start of the next run, and at the end of `scripts/check.ps1`.

"Finished" is decided by the lock itself, not guessed from age: a folder is
renamed before it is deleted, and Windows refuses to rename a folder that
holds an open file, so a run still in progress (in another terminal, or a
mutation check) is left alone. On POSIX nothing is locked and the exit
cleanup already works, so there only folders older than `STALE_AFTER_S`
(a crashed run) are swept.

Set KEEP_TEST_FILES=1 to keep a run's folder, e.g. to look at a failure.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path

ROOT_NAME = "assistant-studio-tests"
STALE_AFTER_S = 3 * 3600


def root(base: Path | None = None) -> Path:
    return (base or Path(tempfile.gettempdir())) / ROOT_NAME


def sweep(base: Path | None = None) -> int:
    """Delete finished runs' folders; how many were deleted."""
    parent = root(base)
    if not parent.is_dir():
        return 0
    deleted = 0
    for run in parent.glob("run-*"):
        if os.name != "nt" and time.time() - run.stat().st_mtime < STALE_AFTER_S:
            continue
        doomed = run.with_name(f"deleting-{run.name}")
        try:
            run.rename(doomed)  # fails on Windows while a file inside is open
        except OSError:
            continue
        shutil.rmtree(doomed, ignore_errors=True)
        deleted += 1
    for leftover in parent.glob("deleting-*"):  # an earlier sweep interrupted
        shutil.rmtree(leftover, ignore_errors=True)
    return deleted


def new_run_dir() -> Path:
    parent = root()
    parent.mkdir(exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="run-", dir=parent))


if __name__ == "__main__":
    print(f"removed {sweep()} finished test run folder(s)")
