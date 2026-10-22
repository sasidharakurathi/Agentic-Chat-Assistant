"""Turn off someone's two-step sign-in (Phase 8.4), when they have lost both
their phone and their recovery codes.

For the operator, on the server (from apps/api, or in the api container):

    python -m scripts.reset_two_factor someone@company.example

Check who is asking first, in person or on a channel you trust: this is the
one way around the second factor. They sign in with their password and set
it up again. Recorded in the audit log.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.db.session import get_sessionmaker
from app.models.user import User
from app.services import two_factor
from sqlalchemy import select


async def _main(email: str) -> int:
    async with get_sessionmaker()() as session:
        user = await session.scalar(select(User).where(User.email == email.strip().lower()))
        if user is None:
            sys.stderr.write("No account has that address.\n")
            return 1
        if not await two_factor.enabled(session, user.id):
            sys.stderr.write("That account has no two-step sign-in to turn off.\n")
            return 1
        await two_factor.reset(session, user)
    print(f"Two-step sign-in is off for {email}. They can sign in with their password.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("email")
    return asyncio.run(_main(parser.parse_args().email))


if __name__ == "__main__":
    sys.exit(main())
