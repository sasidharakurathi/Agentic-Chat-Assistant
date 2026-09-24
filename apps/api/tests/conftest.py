from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

# Must be set before anything under app.* imports app.config.
_TMP = Path(tempfile.mkdtemp(prefix="assistant-studio-test-"))
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TMP / 'test.db'}")
os.environ.setdefault("JWT_SECRET", "test-secret-value-that-is-long-enough-xxxxxxxx")
os.environ.setdefault("LOG_FORMAT", "console")


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
