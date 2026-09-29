"""Choose an MCP server's tools for an assistant, from the command line.

A development stand-in for the canvas editor (task 4.10), so the chat side
of Phase 4 can be tested by hand now. It changes the assistant's **draft**
exactly as the editor will: the config's `mcp_servers` entry and, through
the normal projection, the `mcp_server` node on the canvas.

    python -m scripts.mcp_allow --assistant "MCP Lab" --server echo --tools echo,environment
    python -m scripts.mcp_allow --assistant "MCP Lab" --server echo --rule echo=auto
    python -m scripts.mcp_allow --assistant "MCP Lab" --server echo --server-rule require
    python -m scripts.mcp_allow --assistant "MCP Lab" --default deny
    python -m scripts.mcp_allow --assistant "MCP Lab" --show

Rules are `auto`, `require` (ask a person) or `deny`, or `clear` to remove
one. `--tools` replaces the allowlist; `--rule` and `--server-rule` change
only what they name. Refuses to run in production.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid

from app.config import settings
from app.db.session import get_sessionmaker
from app.models.assistant import Assistant
from app.models.integration import McpServer
from app.schemas.assistant_config import AssistantConfig, McpServerRef
from app.services.assistants import save_draft_config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

MODES = ("auto", "require", "deny", "clear")


async def _find_assistant(session: AsyncSession, key: str) -> Assistant:
    try:
        found = await session.get(Assistant, uuid.UUID(key))
        if found is not None:
            return found
    except ValueError:
        pass
    rows = (await session.scalars(select(Assistant).where(Assistant.name == key))).all()
    if len(rows) == 1:
        return rows[0]
    if not rows:
        raise SystemExit(f"No assistant named {key!r}. Use its name or id from the app's URL.")
    ids = ", ".join(str(r.id) for r in rows)
    raise SystemExit(f"Several assistants are named {key!r}; pass one of these ids: {ids}")


def _show(config: AssistantConfig, names: dict[str, str]) -> None:
    print(f"mcp_default: {config.approval_policy.mcp_default}")
    if not config.mcp_servers:
        print("no MCP servers wired to this assistant")
    for ref in config.mcp_servers:
        print(
            json.dumps(
                {
                    "server": names.get(ref.id, ref.id),
                    "tools": ref.tools,
                    "server_rule": ref.approval,
                    "tool_rules": ref.tool_approvals,
                },
                indent=2,
            )
        )


def _apply_server(
    args: argparse.Namespace,
    config: AssistantConfig,
    servers: list[McpServer],
    names: dict[str, str],
) -> None:
    """Change one server's allowlist and rules in `config`, in place."""
    server = next((s for s in servers if s.name == args.server), None)
    if server is None:
        known = ", ".join(sorted(names.values())) or "none"
        raise SystemExit(f"No MCP server {args.server!r} on this assistant (have: {known}).")
    ref = next((r for r in config.mcp_servers if r.id == str(server.id)), None)
    if ref is None:
        ref = McpServerRef(id=str(server.id))
        config.mcp_servers.append(ref)
    if args.tools is not None:
        wanted = [t.strip() for t in args.tools.split(",") if t.strip()]
        offered = {t.get("name") for t in server.tools or []}
        missing = [t for t in wanted if t not in offered]
        if missing:
            print(f"note: not among the discovered tools (press Discover tools?): {missing}")
        ref.tools = sorted(set(wanted))
    if args.server_rule:
        ref.approval = None if args.server_rule == "clear" else args.server_rule
    for item in args.rule or []:
        tool, _, mode = item.partition("=")
        if mode not in MODES:
            raise SystemExit(f"--rule {item!r}: use tool=auto|require|deny|clear")
        if mode == "clear":
            ref.tool_approvals.pop(tool, None)
        else:
            ref.tool_approvals[tool] = mode


async def main(args: argparse.Namespace) -> None:
    if settings.is_production:
        raise SystemExit("mcp_allow is a development tool; it refuses to run in production.")
    async with get_sessionmaker()() as session:
        assistant = await _find_assistant(session, args.assistant)
        config = AssistantConfig.model_validate(assistant.draft_config)
        servers = list(
            (
                await session.scalars(
                    select(McpServer).where(McpServer.assistant_id == assistant.id)
                )
            ).all()
        )
        names = {str(s.id): s.name for s in servers}

        if args.default:
            config.approval_policy.mcp_default = args.default

        if args.server:
            _apply_server(args, config, servers, names)

        if not args.show:
            config = AssistantConfig.model_validate(config.model_dump())
            await save_draft_config(session, assistant, config)
            print(f"saved the draft of {assistant.name!r}")
        _show(config, names)


def cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--assistant", required=True, help="name or id")
    parser.add_argument("--server", help="the MCP server's name, as in the MCP tab")
    parser.add_argument("--tools", help="comma-separated allowlist (replaces it; '' for none)")
    parser.add_argument("--rule", action="append", help="tool=auto|require|deny|clear")
    parser.add_argument("--server-rule", choices=MODES)
    parser.add_argument("--default", choices=("auto", "require", "deny"), help="mcp_default")
    parser.add_argument("--show", action="store_true", help="print the current settings only")
    asyncio.run(main(parser.parse_args()))


if __name__ == "__main__":
    cli()
