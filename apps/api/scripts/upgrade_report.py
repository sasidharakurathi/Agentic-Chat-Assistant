"""What the upgrade to Phase 7b carried over, and what needs a decision
(Phase 7b.8).

For the operator, on the server (from apps/api, or in the api container):

    python -m scripts.upgrade_report                 # read-only
    python -m scripts.upgrade_report archive <org id> --reason "..."
    python -m scripts.upgrade_report unarchive <org id>
    python -m scripts.upgrade_report freeze <org id>

Run the report on a copy of the database before upgrading the real one
(OPERATIONS §7), and again after. Archiving sets an organisation aside:
nobody reaches it, nothing in it is deleted, and `unarchive` brings it
back. Freezing keeps it readable and stops all spend there. Each action is
in that organisation's audit log.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

from app.api.errors import AppError
from app.config import settings
from app.db.session import get_sessionmaker
from app.services import rollout


def _print(report: rollout.Report) -> None:
    print(f"ORG_MODE={settings.org_mode}\n")
    for o in report.orgs:
        kind = "personal" if o.personal else "team"
        state = f"ARCHIVED ({o.archived})" if o.archived else "open"
        print(f"{o.name}  [{kind}, {state}]  id {o.id}")
        print(f"  owners: {', '.join(o.owners) or 'none who can sign in'}")
        print(
            f"  {o.members} member(s); assistants {o.live} live, {o.closed} closed, "
            f"{o.paused} paused; {o.conversations} conversation(s); ${o.spent_usd:.2f} spent"
        )
    print()
    for level, text in report.notes:
        print(f"[{level}] {text}")


async def _main(args: argparse.Namespace) -> int:
    async with get_sessionmaker()() as session:
        try:
            if args.command in (None, "report"):
                _print(await rollout.report(session))
                return 0
            org_id = uuid.UUID(args.org)
            if args.command == "archive":
                left = await rollout.stranded(session, org_id)
                if left and not args.force:
                    sys.stderr.write(
                        "These people would have no organisation to sign in to: "
                        f"{', '.join(left)}. Add them elsewhere first, or pass --force.\n"
                    )
                    return 1
                org = await rollout.archive(session, org_id, reason=args.reason)
                print(f"Archived {org.name}.")
            elif args.command == "unarchive":
                org = await rollout.unarchive(session, org_id)
                print(f"{org.name} is open again.")
            elif args.command == "freeze":
                await rollout.freeze(session, org_id)
                print("Frozen: its day and month limits are 0. Raise them to lift it.")
        except (AppError, ValueError) as exc:
            sys.stderr.write(f"{getattr(exc, 'message', exc)}\n")
            return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("report")
    a = sub.add_parser("archive")
    a.add_argument("org")
    a.add_argument("--reason", required=True)
    a.add_argument("--force", action="store_true")
    for name in ("unarchive", "freeze"):
        sub.add_parser(name).add_argument("org")
    return asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
