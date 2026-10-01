"""Checking MCP servers and discovering their tools (task 4.5).

Real servers throughout: the echo server behind a real runner (stdio), and
the same server over streamable HTTP. The pinned transport and the error
messages are also tested on their own.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from app.config import settings
from app.db.session import get_sessionmaker
from app.models.integration import McpServer
from app.security.pinned_transport import PinnedTransport
from app.security.ssrf import SsrfBlocked
from app.services.mcp_discovery import McpUnavailable, describe, normalize_tools
from httpx import AsyncClient

pytestmark = pytest.mark.anyio

ECHO = Path(__file__).resolve().parent / "fixtures" / "mcp_echo_server.py"
RUNNER_TOKEN = "runner-test-token-0123456789"  # as in conftest.py
ECHO_TOOLS = ["echo", "environment", "spin"]


async def _assistant(client: AsyncClient, headers: dict[str, str]) -> str:
    r = await client.post("/api/v1/assistants", json={"name": "MCP"}, headers=headers)
    return str(r.json()["id"])


async def _register(client: AsyncClient, auth: dict[str, str], aid: str, **body: Any) -> str:
    r = await client.post(f"/api/v1/assistants/{aid}/mcp-servers", json=body, headers=auth)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _act(client: AsyncClient, auth: dict[str, str], aid: str, sid: str, verb: str) -> dict:
    r = await client.post(f"/api/v1/assistants/{aid}/mcp-servers/{sid}:{verb}", headers=auth)
    assert r.status_code == 200, r.text
    return dict(r.json())


async def _get(client: AsyncClient, auth: dict[str, str], aid: str, sid: str) -> dict:
    return dict(
        (await client.get(f"/api/v1/assistants/{aid}/mcp-servers/{sid}", headers=auth)).json()
    )


@pytest.fixture
def use_runner(runner: str, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "mcp_runner_url", runner)
    monkeypatch.setattr(settings, "mcp_runner_token", RUNNER_TOKEN)
    return runner


# ── the catalog is untrusted input ───────────────────────────


def _tool(name: str, **extra: Any) -> Any:
    return SimpleNamespace(
        name=name,
        description=extra.get("description", "does a thing"),
        input_schema=extra.get("schema", {"type": "object"}),
        annotations=SimpleNamespace(read_only_hint=extra.get("read_only")),
    )


def test_the_catalog_is_validated_and_capped() -> None:
    tools, skipped = normalize_tools(
        [
            _tool("search", read_only=True),
            _tool("search"),  # twice
            _tool("bad name!"),
            _tool("x" * 65),
            _tool("huge", schema={"type": "object", "description": "y" * 40_000}),
            _tool("long", description="z" * 5000),
        ]
    )
    assert [t["name"] for t in tools] == ["search", "long"]
    assert tools[0]["read_only"] is True
    assert len(tools[1]["description"]) == 2000
    assert len(skipped) == 4
    assert any("listed twice" in s for s in skipped)
    assert any("input schema is larger" in s for s in skipped)


# ── errors a builder can act on ──────────────────────────────


def _status_error(code: int) -> httpx2.HTTPStatusError:
    request = httpx2.Request("POST", "https://mcp.example.com/mcp")
    return httpx2.HTTPStatusError(
        "x", request=request, response=httpx2.Response(code, request=request)
    )


def test_errors_are_explained_even_inside_exception_groups() -> None:
    nested = BaseExceptionGroup("outer", [ExceptionGroup("inner", [_status_error(401)])])
    assert "refused the credentials (HTTP 401)" in describe(nested)
    assert "Redirects are not followed" in describe(_status_error(307))
    assert describe(SsrfBlocked("10.0.0.1 is not a public address")) == (
        "10.0.0.1 is not a public address"
    )
    assert describe(TimeoutError()) == "The server did not answer in time."
    leaked = describe(RuntimeError("token sk-ant-" + "a" * 30 + " rejected"))
    assert "sk-ant-" + "a" * 30 not in leaked


# ── the pinned transport ─────────────────────────────────────


class _Recorder(httpx2.AsyncBaseTransport):
    def __init__(self) -> None:
        self.seen: list[httpx2.Request] = []

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.seen.append(request)
        return httpx2.Response(200, json={})


def _resolver(table: dict[str, list[str]]):
    async def resolve(host: str, _port: int) -> list[str]:
        return table[host]

    return resolve


async def test_every_request_is_checked_and_pinned() -> None:
    inner = _Recorder()
    transport = PinnedTransport(
        resolver=_resolver({"mcp.example.com": ["93.184.216.34"], "evil.test": ["10.0.0.9"]}),
        inner=inner,
    )
    async with httpx2.AsyncClient(transport=transport) as client:
        await client.post("https://mcp.example.com/mcp", json={})
        with pytest.raises(SsrfBlocked, match="no public address"):
            await client.post("https://evil.test/mcp", json={})
        with pytest.raises(SsrfBlocked, match="over https"):
            await client.post("http://mcp.example.com/mcp", json={})
    (sent,) = inner.seen
    assert sent.url.host == "93.184.216.34"
    assert sent.headers["host"] == "mcp.example.com"
    assert sent.extensions["sni_hostname"] == "mcp.example.com"


# ── stdio, through the runner ────────────────────────────────


async def test_a_local_command_is_discovered_through_the_runner(
    client: AsyncClient, org_headers: dict[str, str], use_runner: str
) -> None:
    aid = await _assistant(client, org_headers)
    sid = await _register(
        client,
        org_headers,
        aid,
        name="echo",
        transport="stdio",
        command=sys.executable,
        args=[str(ECHO)],
    )
    health = await _act(client, org_headers, aid, sid, "health")
    assert health["ok"] is True, health

    found = await _act(client, org_headers, aid, sid, "discover-tools")
    assert found["ok"] is True and found["tool_count"] == 3, found
    server = await _get(client, org_headers, aid, sid)
    assert sorted(t["name"] for t in server["tools"]) == ECHO_TOOLS
    assert server["status"] == "ok" and server["error"] is None
    assert server["tools_discovered_at"] and server["last_checked_at"]
    echo = next(t for t in server["tools"] if t["name"] == "echo")
    assert "text" in echo["input_schema"]["properties"]


async def test_a_failure_is_recorded_and_the_old_tools_kept(
    client: AsyncClient, org_headers: dict[str, str], use_runner: str
) -> None:
    aid = await _assistant(client, org_headers)
    sid = await _register(
        client,
        org_headers,
        aid,
        name="echo",
        transport="stdio",
        command=sys.executable,
        args=[str(ECHO)],
    )
    await _act(client, org_headers, aid, sid, "discover-tools")
    async with get_sessionmaker()() as s:
        server = await s.get(McpServer, uuid.UUID(sid))
        assert server is not None
        server.command = "definitely-not-a-real-command-xyz"  # broken, without clearing tools
        await s.commit()

    result = await _act(client, org_headers, aid, sid, "discover-tools")
    assert result["ok"] is False and result["error"]
    server = await _get(client, org_headers, aid, sid)
    assert server["status"] == "error" and server["error"] == result["error"]
    assert len(server["tools"]) == 3, "a failed check keeps what the server offered"


async def test_an_unreachable_runner_is_explained(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "mcp_runner_url", "http://127.0.0.1:9")
    monkeypatch.setattr(settings, "mcp_runner_token", RUNNER_TOKEN)
    aid = await _assistant(client, org_headers)
    sid = await _register(
        client, org_headers, aid, name="echo", transport="stdio", command="npx", args=["x"]
    )
    result = await _act(client, org_headers, aid, sid, "health")
    assert result["ok"] is False
    assert "MCP runner is not reachable" in result["error"]


# ── http ─────────────────────────────────────────────────────


async def test_a_remote_server_is_discovered_over_http(
    client: AsyncClient,
    org_headers: dict[str, str],
    echo_http: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The test server is plain http on this machine: only the local-testing
    # switch allows that, at registration and at connection.
    monkeypatch.setattr(settings, "mcp_allow_insecure_urls", True)
    aid = await _assistant(client, org_headers)
    sid = await _register(
        client,
        org_headers,
        aid,
        name="remote",
        transport="http",
        url=echo_http,
        headers={"X-Api-Key": "k"},
    )
    found = await _act(client, org_headers, aid, sid, "discover-tools")
    assert found["ok"] is True and found["tool_count"] == 3, found

    # With the switch off, the same server is refused before any connection:
    # plain http, on a private address.
    monkeypatch.setattr(settings, "mcp_allow_insecure_urls", False)
    refused = await _act(client, org_headers, aid, sid, "health")
    assert refused["ok"] is False
    assert "https" in refused["error"]


# ── the scheduled sweep ──────────────────────────────────────


async def test_the_sweep_checks_enabled_remote_servers_only(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import worker
    from app.services import mcp_discovery

    monkeypatch.setattr(settings, "mcp_allow_insecure_urls", False)
    aid = await _assistant(client, org_headers)
    on = await _register(
        client, org_headers, aid, name="on", transport="http", url="https://a.example.com/mcp"
    )
    await _register(
        client,
        org_headers,
        aid,
        name="off",
        transport="http",
        url="https://b.example.com/mcp",
        enabled=False,
    )
    await _register(client, org_headers, aid, name="local", transport="stdio", command="npx")

    checked: list[str] = []

    async def fake_check(_db: Any, server: McpServer) -> None:
        checked.append(str(server.id))

    monkeypatch.setattr(mcp_discovery, "check", fake_check)
    await worker.mcp_health_sweep({})
    assert checked == [on]


def test_the_sweep_is_scheduled() -> None:
    from app.worker import WorkerSettings

    assert any(job.name.endswith("mcp_health_sweep") for job in WorkerSettings.cron_jobs)


def test_unavailable_is_a_plain_message() -> None:
    assert describe(McpUnavailable("runner down")) == "runner down"


def test_the_remote_client_never_follows_redirects_or_proxies() -> None:
    """The SDK's default client follows redirects: a public server could send
    the API anywhere. Ours must not, and must ignore proxy variables (a proxy
    would resolve the name itself and undo the pinning)."""
    from app.services.mcp_discovery import _remote_client

    client = _remote_client({"X-Api-Key": "k"})
    assert client.follow_redirects is False
    assert client._trust_env is False
    assert isinstance(client._transport, PinnedTransport)


async def test_discovery_leaves_no_session_running(
    client: AsyncClient, org_headers: dict[str, str], use_runner: str
) -> None:
    import anyio
    import httpx

    aid = await _assistant(client, org_headers)
    sid = await _register(
        client,
        org_headers,
        aid,
        name="echo",
        transport="stdio",
        command=sys.executable,
        args=[str(ECHO)],
    )
    await _act(client, org_headers, aid, sid, "discover-tools")
    live = -1
    for _ in range(20):
        async with httpx.AsyncClient() as http:
            live = (await http.get(f"{use_runner}/healthz")).json()["sessions"]
        if live == 0:
            break
        await anyio.sleep(0.2)
    assert live == 0, "the runner session outlived the discovery"
