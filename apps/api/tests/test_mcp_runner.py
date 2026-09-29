"""The MCP runner and its sandbox (task 4.4).

A real runner process is started with a canary `APP_KEK` in its own
environment, and a real stdio MCP server (tests/fixtures/mcp_echo_server.py)
is reached through it with the MCP SDK's own HTTP client. So these tests
prove the whole path: the runner starts the server, bridges MCP over HTTP,
keeps the runner's environment out of the server, gives the server a private
directory, enforces tokens, and stops sessions on time.

POSIX-only tests check the jail's resource limits; on Windows they skip,
and CI (Linux) runs them.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import anyio
import httpx
import httpx2
import pytest
from app.mcp.limits import SandboxLimits
from app.mcp.runner import JAIL, SessionRequest, child_environment, spawn_parameters
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

pytestmark = pytest.mark.anyio

API_DIR = Path(__file__).resolve().parents[1]
ECHO = API_DIR / "tests" / "fixtures" / "mcp_echo_server.py"
#: Must match RUNNER_TOKEN in conftest.py, which starts the runner.
TOKEN = "runner-test-token-0123456789"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _same_dir(a: str, b: str) -> bool:
    return Path(a).resolve() == Path(b).resolve()


def _gone(path: str) -> bool:
    return not Path(path).exists()


def _auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _start(base: str, **over: Any) -> dict[str, Any]:
    body = {"command": sys.executable, "args": [str(ECHO)], "label": "echo"} | over
    async with httpx.AsyncClient(timeout=60) as http:
        resp = await http.post(f"{base}/sessions", json=body, headers=_auth())
    assert resp.status_code == 201, resp.text
    return dict(resp.json())


async def _call(base: str, info: dict[str, Any], tool: str, args: dict[str, Any]) -> str:
    client = httpx2.AsyncClient(headers=_auth(info["token"]), timeout=60)
    async with (
        streamable_http_client(base + info["path"], http_client=client) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool(tool, args)
        return str(result.content[0].text)  # type: ignore[union-attr]


async def _status(base: str, sid: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10) as http:
        return dict((await http.get(f"{base}/sessions/{sid}", headers=_auth())).json())


# ── pure pieces ──────────────────────────────────────────────


def test_limits_take_defaults_and_survive_bad_stored_values() -> None:
    limits = SandboxLimits.from_row({"memory_mb": 256, "cpu_seconds": -5, "nonsense": 1})
    assert limits.memory_mb == 256
    assert limits.cpu_seconds == SandboxLimits().cpu_seconds
    with pytest.raises(ValueError, match="less than or equal"):
        SandboxLimits(memory_mb=100_000)


def test_the_server_environment_is_its_own_plus_a_private_home(tmp_path: Path) -> None:
    env = child_environment(tmp_path, {"DEMO_TOKEN": "x"})
    assert env["DEMO_TOKEN"] == "x"
    assert env["HOME"] == env["TMPDIR"] == str(tmp_path)
    assert "APP_KEK" not in env and "PATH" not in env  # PATH comes from the MCP client's basics


@pytest.mark.skipif(os.name != "posix", reason="the jail is POSIX-only")
def test_on_posix_the_server_starts_inside_the_jail(tmp_path: Path) -> None:
    params = spawn_parameters(SessionRequest(command="npx", args=["-y", "pkg"]), tmp_path)
    assert params.command == sys.executable
    assert params.args[:2] == ["-I", str(JAIL)]
    assert params.args[3:] == ["--", "npx", "-y", "pkg"]
    assert json.loads(params.args[2])["memory_mb"] == SandboxLimits().memory_mb


@pytest.mark.skipif(os.name != "posix", reason="resource limits are POSIX-only")
def test_the_jail_applies_limits_before_the_command_runs(tmp_path: Path) -> None:
    probe = (
        "import resource as r, os;"
        "print(r.getrlimit(r.RLIMIT_AS)[0], r.getrlimit(r.RLIMIT_NOFILE)[0],"
        " r.getrlimit(r.RLIMIT_CORE)[0], oct(os.umask(0)))"
    )
    limits = json.dumps({"memory_mb": 512, "max_open_files": 64})
    out = subprocess.run(
        [sys.executable, "-I", str(JAIL), limits, "--", sys.executable, "-c", probe],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": os.environ["PATH"]},
    ).stdout.split()
    assert out == [str(512 * 1024 * 1024), "64", "0", "0o77"]


# ── through a real runner ────────────────────────────────────


async def test_a_server_is_reached_through_the_runner(runner: str) -> None:
    info = await _start(runner)
    assert await _call(runner, info, "echo", {"text": "through the runner"}) == (
        "through the runner"
    )
    assert "clean environment" in info["enforced"]["applied"]


async def test_the_runners_secrets_never_reach_the_server(runner: str) -> None:
    info = await _start(runner, env={"DEMO_TOKEN": "given"})
    seen = json.loads(await _call(runner, info, "environment", {}))
    assert seen["leaked"] == [], "the runner's environment leaked into the server"
    assert seen["demo_token"] == "given"
    # A private directory, which is also HOME, and which is gone afterwards.
    assert _same_dir(seen["cwd"], seen["home"])
    assert Path(seen["cwd"]).name.startswith("mcp-")
    for _ in range(50):
        if _gone(seen["cwd"]):
            break
        await anyio.sleep(0.1)
    assert _gone(seen["cwd"]), "the private directory outlived the session"


async def test_each_session_answers_only_to_its_own_token(runner: str) -> None:
    a = await _start(runner)
    b = await _start(runner)
    async with httpx.AsyncClient(timeout=10) as http:
        for token in ("wrong", b["token"]):
            resp = await http.post(runner + a["path"], json={}, headers=_auth(token))
            assert resp.status_code == 401, token
        assert (await http.post(runner + a["path"], json={})).status_code == 401
        # And the control endpoints need the runner token.
        assert (await http.post(f"{runner}/sessions", json={})).status_code == 401
        assert (await http.get(f"{runner}/sessions/{a['session_id']}")).status_code == 401


async def test_sessions_stop_when_idle_and_at_their_time_limit(runner: str) -> None:
    idle = await _start(runner, limits={"idle_timeout_s": 10, "wall_clock_s": 3600})
    capped = await _start(runner, limits={"idle_timeout_s": 3600, "wall_clock_s": 10})
    deadline = time.monotonic() + 30
    ended: dict[str, str] = {}
    while time.monotonic() < deadline and len(ended) < 2:
        for name, info in (("idle", idle), ("capped", capped)):
            status = await _status(runner, info["session_id"])
            if not status.get("running", True):
                ended[name] = str(status.get("ended_reason"))
        await anyio.sleep(0.5)
    assert ended.get("idle", "").startswith("idle for 10s"), ended
    assert "10s time limit" in ended.get("capped", ""), ended


async def test_a_command_that_cannot_start_is_reported(runner: str) -> None:
    async with httpx.AsyncClient(timeout=60) as http:
        resp = await http.post(
            f"{runner}/sessions",
            json={"command": "definitely-not-a-real-mcp-server-xyz", "label": "missing"},
            headers=_auth(),
        )
    assert resp.status_code == 502, resp.text
    assert resp.json()["error"]


# ── the API's client ─────────────────────────────────────────


async def test_the_api_opens_and_closes_sessions(
    runner: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[None] | None:
    from app.config import settings
    from app.models.integration import McpServer, McpTransport
    from app.services import mcp_runner

    monkeypatch.setattr(settings, "mcp_runner_url", runner)
    monkeypatch.setattr(settings, "mcp_runner_token", TOKEN)
    server = McpServer(
        name="echo",
        transport=McpTransport.stdio,
        command=sys.executable,
        args=[str(ECHO)],
        sandbox={"idle_timeout_s": 60},
    )
    session = await mcp_runner.open_session(server, {"DEMO_TOKEN": "api"})
    cfg = session.sdk_config()
    assert cfg["type"] == "http" and cfg["url"].startswith(runner)
    assert (await mcp_runner.session_status(session.session_id))["running"] is True
    await mcp_runner.close_session(session.session_id)
    assert (await mcp_runner.health())["ok"] is True
    return None


async def test_an_unreachable_runner_is_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings
    from app.models.integration import McpServer, McpTransport
    from app.services import mcp_runner

    monkeypatch.setattr(settings, "mcp_runner_url", f"http://127.0.0.1:{free_port()}")
    monkeypatch.setattr(settings, "mcp_runner_token", TOKEN)
    server = McpServer(name="x", transport=McpTransport.stdio, command="npx", args=[])
    with pytest.raises(mcp_runner.RunnerUnavailable, match="not reachable"):
        await mcp_runner.open_session(server, {})


async def test_the_builder_can_see_how_contained_servers_are(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    runner: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "mcp_runner_url", runner)
    body = (await client.get("/api/v1/mcp-runner", headers=auth_headers)).json()
    assert body["reachable"] is True
    assert "clean environment" in body["applied"]
    assert body["full_sandbox"] is (os.name == "posix")

    monkeypatch.setattr(settings, "mcp_runner_url", "")
    off = (await client.get("/api/v1/mcp-runner", headers=auth_headers)).json()
    assert off["reachable"] is False and "MCP_RUNNER_URL" in off["error"]
    assert (await client.get("/api/v1/mcp-runner")).status_code == 401
