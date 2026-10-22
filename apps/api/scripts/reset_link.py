"""Print a password-reset link for an account (Phase 7b.6).

For the operator, on the server, when email can't do it: the server sends
no email, or the owner is locked out. Run from apps/api, or in the api
container:

    python -m scripts.reset_link someone@company.example

The link works once, for PASSWORD_RESET_TTL_MINUTES, and replaces any link
already sent. Give it to the person yourself, in person or on a channel you
trust: whoever holds it can set the password. Issuing it is recorded in the
audit log.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.db.session import get_sessionmaker
from app.models.user import User
from app.models.user_token import TokenPurpose
from app.services import account, audit
from sqlalchemy import select


async def _main(email: str) -> int:
    async with get_sessionmaker()() as session:
        user = await session.scalar(select(User).where(User.email == email.strip().lower()))
        if user is None or not user.is_active:
            sys.stderr.write("No active account has that address.\n")
            return 1
        raw = await account.issue(session, user, TokenPurpose.password_reset)
        await audit.record(
            session,
            action="user.password_reset_link_issued",
            actor_user_id=None,
            target_type="user",
            target_id=user.id,
            meta={"by": "operator"},
        )
        await session.commit()
    print(account.link("/reset-password", raw))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("email")
    return asyncio.run(_main(parser.parse_args().email))


if __name__ == "__main__":
    sys.exit(main())
