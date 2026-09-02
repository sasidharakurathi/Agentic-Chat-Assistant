from __future__ import annotations

from httpx import AsyncClient


async def test_healthz(client: AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body


async def test_readyz_reports_db_ok(client: AsyncClient) -> None:
    resp = await client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["checks"]["database"] == "ok"


async def test_request_id_header_present(client: AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.headers.get("x-request-id")
