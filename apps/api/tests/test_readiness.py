"""`/readyz` (task 0.3): database, Redis, storage and the Claude CLI.

It used to check the database only, so an instance with no Redis or no
bucket reported itself ready. Critical failures are a 503; the rest leave the
instance serving, marked degraded.
"""

from __future__ import annotations

import asyncio

import pytest
from app.agent.driver import ClaudeSDKDriver, FakeDriver
from app.api.routes import health
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


async def _ok() -> str:
    return "ok"


async def _boom() -> str:
    raise ConnectionError("refused")


async def _hang() -> str:
    await asyncio.sleep(30)
    return "ok"  # pragma: no cover


@pytest.fixture
def all_up(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setattr(health, "_redis", _ok)
    monkeypatch.setattr(health, "_storage", _ok)
    monkeypatch.setattr(health, "_agent_cli", _ok)
    monkeypatch.setattr(health, "_mcp_runner", _ok)
    return monkeypatch


async def test_every_dependency_is_reported(
    client: AsyncClient, all_up: pytest.MonkeyPatch
) -> None:
    resp = await client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ok",
        "checks": {
            "database": "ok",
            "redis": "ok",
            "storage": "ok",
            "agent_cli": "ok",
            "mcp_runner": "ok",
        },
    }


@pytest.mark.parametrize("check", ["_redis", "_storage", "_mcp_runner"])
async def test_a_degradable_dependency_down_is_degraded_not_unready(
    client: AsyncClient, all_up: pytest.MonkeyPatch, check: str
) -> None:
    all_up.setattr(health, check, _boom)
    resp = await client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"][check.lstrip("_")] == "error: ConnectionError"


async def test_a_missing_cli_with_the_real_driver_is_unready(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "_redis", _ok)
    monkeypatch.setattr(health, "_storage", _ok)
    monkeypatch.setattr(health, "get_driver", ClaudeSDKDriver)
    monkeypatch.setattr(health, "claude_cli_path", lambda: None)
    resp = await client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["checks"]["agent_cli"] == "error: Claude CLI not found"


async def test_the_cli_is_not_required_for_the_offline_driver(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "_redis", _ok)
    monkeypatch.setattr(health, "_storage", _ok)
    monkeypatch.setattr(health, "get_driver", FakeDriver)
    monkeypatch.setattr(health, "claude_cli_path", lambda: None)
    resp = await client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["checks"]["agent_cli"] == "skipped: offline driver"


async def test_a_hung_dependency_times_out_instead_of_hanging_the_probe(
    client: AsyncClient, all_up: pytest.MonkeyPatch
) -> None:
    all_up.setattr(health, "CHECK_TIMEOUT_S", 0.2)
    all_up.setattr(health, "_storage", _hang)
    resp = await asyncio.wait_for(client.get("/readyz"), 5)
    assert resp.json()["checks"]["storage"] == "error: timed out"


def test_the_sdks_bundled_cli_is_found() -> None:
    """The real lookup, against the installed SDK (it bundles a native CLI)."""
    path = health.claude_cli_path()
    assert path is not None and "claude" in path.lower()


async def test_a_loop_that_cannot_spawn_is_unready_for_the_real_driver(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """uvicorn --reload on Windows picks a loop that cannot start the CLI."""
    monkeypatch.setattr(health, "_redis", _ok)
    monkeypatch.setattr(health, "_storage", _ok)
    monkeypatch.setattr(health, "get_driver", ClaudeSDKDriver)
    monkeypatch.setattr(health, "loop_can_spawn", lambda: False)
    resp = await client.get("/readyz")
    assert resp.status_code == 503
    assert "without --reload" in resp.json()["checks"]["agent_cli"]
