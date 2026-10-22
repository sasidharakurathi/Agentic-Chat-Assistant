"""Putting an assistant live in a test (Phase 7b.1): submit the draft, then
approve it. Chat answers only from an approved version, so a test that
chats as an employee would rather than as the builder starts here; one
testing the builder's own draft opens a preview instead (`PREVIEW`)."""

from __future__ import annotations

from httpx import AsyncClient

API = "/api/v1"

#: The body that opens a preview: the builder's test chat on the draft.
PREVIEW = {"mode": "preview"}


async def submit(client: AsyncClient, aid: str, headers: dict[str, str], note: str = "") -> int:
    r = await client.post(f"{API}/assistants/{aid}/versions", json={"note": note}, headers=headers)
    assert r.status_code == 201, r.text
    return int(r.json()["version_number"])


async def go_live(
    client: AsyncClient,
    aid: str,
    headers: dict[str, str],
    note: str = "",
    approver: dict[str, str] | None = None,
) -> int:
    """Submit the draft as `headers` and approve it as `approver`. By
    default the same person: then they must be the owner with no other admin
    in the org (Phase 7b.3), which is what a test's single account is, and
    their approval carries the note that self-approval requires."""
    number = await submit(client, aid, headers, note)
    r = await client.post(
        f"{API}/assistants/{aid}/versions/{number}/approve",
        json={"note": "Approved in a test"},
        headers=approver or headers,
    )
    assert r.status_code == 200, r.text
    return number
