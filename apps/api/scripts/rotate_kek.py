"""Rotate APP_KEK: re-wrap every stored secret under a new key.

Run from apps/api, with the *current* key still in APP_KEK:

    NEW_APP_KEK=<new base64 key> python -m scripts.rotate_kek --dry-run
    NEW_APP_KEK=<new base64 key> python -m scripts.rotate_kek

then set APP_KEK to the new key and restart the API and workers. Until the
restart, the running processes still hold the old key and cannot open the
re-wrapped secrets, so do it in a maintenance window, or stop them first.

Generate a key with:

    python -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"

The new key is read from the environment, never from the command line (which
shell history and process listings keep), and neither key is ever printed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from app.config import settings
from app.db.session import get_sessionmaker
from app.security.rotation import RotationError, rotate_kek


async def _main(dry_run: bool) -> int:
    new_kek = os.environ.get("NEW_APP_KEK", "").strip()
    if not new_kek:
        sys.stderr.write("NEW_APP_KEK is not set.\n")
        return 2
    async with get_sessionmaker()() as session:
        try:
            report = await rotate_kek(
                session, old_kek=settings.app_kek, new_kek=new_kek, dry_run=dry_run
            )
        except RotationError as exc:
            sys.stderr.write(f"rotation aborted: {exc}\n")
            return 1
    verb = "would rotate" if dry_run else "rotated"
    sys.stdout.write(
        f"{verb} {report.rotated} secret(s); {report.already_current} already on the new key.\n"
    )
    if not dry_run:
        sys.stdout.write("Now set APP_KEK to the new key and restart the API and workers.\n")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="check every secret, change none")
    raise SystemExit(asyncio.run(_main(parser.parse_args().dry_run)))


if __name__ == "__main__":
    main()
