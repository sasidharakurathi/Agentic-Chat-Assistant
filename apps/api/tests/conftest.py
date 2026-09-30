from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

from tests.temp_dirs import new_run_dir, sweep

# Everything a test run writes to "temp" goes into one folder,
# %TEMP%/assistant-studio-tests/run-XXXX, removed when the run ends; finished
# runs' leftovers are swept first (see tests/temp_dirs.py). They used to
# scatter across the system temp directory and were never removed: hundreds
# of folders. KEEP_TEST_FILES=1 keeps a run's folder.
# Must happen before anything under app.* imports app.config.
sweep()
_TMP = new_run_dir()
for _var in ("TMP", "TEMP", "TMPDIR"):
    os.environ[_var] = str(_TMP)
tempfile.tempdir = str(_TMP)
if not os.environ.get("KEEP_TEST_FILES"):
    atexit.register(shutil.rmtree, _TMP, ignore_errors=True)
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TMP / 'test.db'}")
os.environ.setdefault("JWT_SECRET", "test-secret-value-that-is-long-enough-xxxxxxxx")
os.environ.setdefault("LOG_FORMAT", "console")
# The suite makes hundreds of requests from one address in seconds; rate
# limits are off unless a test turns them on (test_rate_limit.py).
os.environ.setdefault("RATE_LIMIT_ENABLED", "0")
# Tests that need an MCP runner start their own; nothing else should reach one.
os.environ.setdefault("MCP_RUNNER_URL", "")


import pytest  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_engine, get_sessionmaker  # noqa: E402
from app.main import app  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402


@pytest.fixture(autouse=True)
async def _fresh_schema() -> AsyncIterator[None]:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """A raw session for tests that hit models directly, bypassing the API."""
    async with get_sessionmaker()() as session:
        yield session


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def registered(client: AsyncClient) -> dict[str, str]:
    """Register a user and return its token pair + email."""
    email = "user@example.com"
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "supersecret", "name": "Test User"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return {"email": email, **data}


@pytest.fixture
def auth_headers(registered: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {registered['access_token']}"}


@pytest.fixture
async def org_headers(client: AsyncClient, registered: dict[str, str]) -> dict[str, str]:
    """Auth headers + the X-Org-Id of the user's personal org."""
    h = {"Authorization": f"Bearer {registered['access_token']}"}
    me = await client.get("/api/v1/auth/me", headers=h)
    org_id = me.json()["memberships"][0]["org_id"]
    return {**h, "X-Org-Id": org_id}


# ── MCP (Phase 4) ────────────────────────────────────────────

import socket  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from collections.abc import Iterator  # noqa: E402

import httpx  # noqa: E402

API_DIR = Path(__file__).resolve().parents[1]
ECHO_SERVER = API_DIR / "tests" / "fixtures" / "mcp_echo_server.py"
RUNNER_TOKEN = "runner-test-token-0123456789"
#: Put in the runner's own environment; a server must never see it.
CANARY = "kek-canary-must-not-reach-the-server"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _serve(args: list[str], url: str, env: dict[str, str] | None = None) -> subprocess.Popen[bytes]:
    proc = subprocess.Popen(
        args,
        cwd=API_DIR,
        env=env or dict(os.environ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(150):
        try:
            httpx.get(url, timeout=1)
            return proc
        except httpx.HTTPError:
            time.sleep(0.1)
    proc.terminate()
    raise RuntimeError(f"{args} did not start")


@pytest.fixture(scope="module")
def runner() -> Iterator[str]:
    """A real MCP runner (task 4.4), with a canary secret in its environment."""
    port = free_port()
    env = {
        **os.environ,
        "MCP_RUNNER_TOKEN": RUNNER_TOKEN,
        "MCP_RUNNER_PORT": str(port),
        "APP_KEK": CANARY,
        "JWT_SECRET": CANARY,
    }
    base = f"http://127.0.0.1:{port}"
    proc = _serve([sys.executable, "-m", "app.mcp.runner"], f"{base}/healthz", env)
    try:
        yield base
    finally:
        proc.terminate()
        proc.wait(10)


@pytest.fixture(scope="module")
def echo_http() -> Iterator[str]:
    """The echo MCP server over streamable HTTP; yields its endpoint URL."""
    port = free_port()
    proc = _serve(
        [sys.executable, str(ECHO_SERVER), "http", str(port)], f"http://127.0.0.1:{port}/mcp"
    )
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        proc.wait(10)
