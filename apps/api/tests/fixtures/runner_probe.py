"""Probe a running MCP runner from inside its own container (task 4.4).

CI runs this against the hardened runner container (and so can you):

    docker exec <runner> python /fixtures/runner_probe.py

It starts the echo server through the runner, calls it, and checks what the
server could see and reach. Exits non-zero, saying why, if any protection is
missing: the full set of limits, an environment free of the runner's
secrets, a private directory, and no route to the internet.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request

import httpx2  # the runner image has only what the MCP SDK brings
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

BASE = "http://127.0.0.1:8100"
AUTH = {"Authorization": f"Bearer {os.environ['MCP_RUNNER_TOKEN']}"}
REQUIRED = {"memory", "cpu time", "processes", "no new privileges", "clean environment"}


async def main() -> list[str]:
    problems: list[str] = []
    health = httpx2.get(f"{BASE}/healthz").json()
    print("health:", health)
    missing = REQUIRED - set(health.get("applied", []))
    if missing:
        problems.append(f"protections not applied: {sorted(missing)}")

    info = httpx2.post(
        f"{BASE}/sessions",
        headers=AUTH,
        json={
            "command": "python",
            "args": ["/fixtures/mcp_echo_server.py"],
            "env": {"DEMO_TOKEN": "container"},
            "limits": {"memory_mb": 256},
        },
        timeout=60,
    ).json()
    client = httpx2.AsyncClient(headers={"Authorization": f"Bearer {info['token']}"}, timeout=60)
    async with (
        streamable_http_client(BASE + info["path"], http_client=client) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        echo_result = await session.call_tool("echo", {"text": "hello"})
        env_result = await session.call_tool("environment", {})
    echo = echo_result.content[0].text  # type: ignore[union-attr]
    seen = json.loads(env_result.content[0].text)  # type: ignore[union-attr]
    print("echo:", echo)
    print("server saw:", seen)
    if echo != "hello":
        problems.append("the echo server did not answer through the runner")
    if seen["leaked"]:
        problems.append(f"runner secrets visible to the server: {seen['leaked']}")
    if seen["cwd"] != seen["home"] or not seen["cwd"].startswith("/tmp/mcp-"):
        problems.append(f"no private directory: {seen}")

    try:
        await asyncio.to_thread(urllib.request.urlopen, "https://pypi.org", timeout=5)
        problems.append("the runner can reach the internet")
    except OSError as exc:
        print("internet: unreachable", type(exc).__name__)
    return problems


if __name__ == "__main__":
    found = asyncio.run(main())
    for problem in found:
        print("FAIL:", problem, file=sys.stderr)
    sys.exit(1 if found else 0)
