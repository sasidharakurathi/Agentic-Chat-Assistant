"""The MCP catalog (task 4.8): every preset must be a valid registration."""

from __future__ import annotations

import pytest
from app.mcp.presets import PRESETS
from app.schemas.mcp_server import McpServerCreate
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("preset", PRESETS, ids=[p.key for p in PRESETS])
def test_each_preset_passes_the_registration_rules(preset) -> None:  # type: ignore[no-untyped-def]
    """As the form would submit it, with placeholder secrets filled in. A
    preset the API would refuse (a bad name, plain http, a credential in its
    arguments) fails here instead of in front of a builder."""
    McpServerCreate.model_validate(
        {
            "name": preset.name,
            "transport": preset.transport,
            "command": preset.command,
            "args": list(preset.args),
            "url": preset.url,
            "env": {f.name: "placeholder" for f in preset.env},
            "headers": {f.name: "placeholder" for f in preset.headers},
        }
    )
    assert preset.docs_url.startswith("https://")


def test_keys_and_names_are_unique() -> None:
    assert len({p.key for p in PRESETS}) == len(PRESETS)
    assert len({p.name for p in PRESETS}) == len(PRESETS)


async def test_the_catalog_is_served_without_values(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    r = await client.get("/api/v1/mcp-presets", headers=org_headers)
    assert r.status_code == 200, r.text
    github = next(p for p in r.json() if p["key"] == "github")
    assert github["headers"][0]["name"] == "Authorization"
    assert "value" not in github["headers"][0]
    assert (await client.get("/api/v1/mcp-presets")).status_code == 401
