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
from app.db.session import get_engine  # noqa: E402
from app.main import app  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402


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
