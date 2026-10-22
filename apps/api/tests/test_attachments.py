"""Screenshots in chat, and secrets typed into it (Phase 9.6).

- A credential typed into a message is masked before the message is stored
  or reaches the model, and the person is told, with the answer.
- A screenshot is uploaded into the conversation first: only images (by
  their bytes, not the browser's claim), up to 5 MB. It is sent with one
  message of that conversation, once, and the model receives it.
- The API serves it back (no presigned storage link, so no bucket CORS)
  to whoever may read the conversation, as an image that can't be sniffed
  into anything else; erasing the conversation deletes it from storage.
- One attached but never sent is deleted after a day.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.db.session import get_sessionmaker
from app.models.attachment import Attachment
from app.models.conversation import Conversation, Message, MessageRole
from app.security.redact import REDACTED, mask_typed_secrets
from app.services import retention
from app.services.attachments import MAX_BYTES, sniff, sweep_unsent
from app.services.chat import SECRET_HIDDEN
from httpx import AsyncClient
from sqlalchemy import select

from test_orgs import _register  # type: ignore[import-not-found]
from test_turns import _conversation, _events, _finish  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"
KEY = "sk-ant-" + "x" * 30  # shaped like a key, not one
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Object storage in a dict: what was put, and what was deleted."""
    objects: dict[str, bytes] = {}
    deleted: list[str] = []

    async def put(key: str, body: bytes, content_type: str) -> None:
        objects[key] = body

    async def get(key: str) -> bytes:
        return objects[key]

    async def delete(key: str) -> None:
        deleted.append(key)
        objects.pop(key, None)

    monkeypatch.setattr("app.services.attachments.put_object", put)
    monkeypatch.setattr("app.services.attachments.get_object", get)
    monkeypatch.setattr("app.services.attachments.delete_object", delete)
    return {"objects": objects, "deleted": deleted}


async def _upload(
    client: AsyncClient, h: dict[str, str], cid: str, body: bytes = PNG, claimed: str = "image/png"
) -> Any:
    return await client.post(
        f"{API}/conversations/{cid}/attachments",
        files={"file": ("shot.png", body, claimed)},
        headers=h,
    )


async def _messages(cid: str) -> list[Message]:
    async with get_sessionmaker()() as s:
        return list(
            (
                await s.scalars(
                    select(Message)
                    .where(Message.conversation_id == uuid.UUID(cid))
                    .order_by(Message.created_at)
                )
            ).all()
        )


async def _attachment(aid: str) -> Attachment:
    async with get_sessionmaker()() as s:
        row = await s.get(Attachment, uuid.UUID(aid))
        assert row is not None
        return row


# ── secrets typed into chat ──────────────────────────────────


def test_typed_credentials_are_masked_and_prose_survives() -> None:
    masked, hidden = mask_typed_secrets(f"my key is {KEY} and password: Hunter2!")
    assert hidden and KEY not in masked and "Hunter2" not in masked
    assert masked.endswith(f"password: {REDACTED}!"), "the full stop stays"
    assert mask_typed_secrets("my pin is 4821")[0] == f"my pin is {REDACTED}"
    for prose in (
        "I forgot my password: can you reset it?",
        "the token is invalid",
        "My password is Expired, what now?",
    ):
        assert mask_typed_secrets(prose) == (prose, False), prose


async def test_a_key_in_a_message_is_never_stored_or_sent(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    cid = await _conversation(client, org_headers)
    r = await client.post(
        f"{API}/conversations/{cid}/messages",
        json={"text": f"Here is my key {KEY} and password: Hunter2!"},
        headers=org_headers,
    )
    assert r.status_code == 200, r.text
    assert KEY not in r.text and "Hunter2" not in r.text, "not even streamed back"
    notes = [e for e in _events(r.text) if e["type"] == "guardrail" and e["check"] == "secret"]
    assert notes == [{"type": "guardrail", "check": "secret", "where": "user_message",
                      "detail": SECRET_HIDDEN, "tool": None}]  # fmt: skip
    await _finish()

    asked, answer = await _messages(cid)
    assert asked.role is MessageRole.user
    assert KEY not in asked.content and "Hunter2" not in asked.content
    assert REDACTED in asked.content
    # The fake driver echoes what it received: the masked text.
    assert answer.role is MessageRole.assistant
    assert "you said:" in answer.content and REDACTED in answer.content
    assert KEY not in answer.content and "Hunter2" not in answer.content
    # Said again after a reload: kept with the answer.
    assert {"type": "guardrail", "check": "secret", "where": "user_message",
            "detail": SECRET_HIDDEN} in answer.blocks  # fmt: skip


async def test_a_message_with_no_secret_has_no_note(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    cid = await _conversation(client, org_headers)
    r = await client.post(
        f"{API}/conversations/{cid}/messages", json={"text": "hello there"}, headers=org_headers
    )
    assert not [e for e in _events(r.text) if e["type"] == "guardrail"]
    await _finish()


# ── uploading ────────────────────────────────────────────────


def test_images_are_known_by_their_bytes() -> None:
    assert sniff(PNG) == ("image/png", "png")
    assert sniff(JPEG) == ("image/jpeg", "jpg")
    assert sniff(b"GIF89a" + b"\x00" * 8) == ("image/gif", "gif")
    assert sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == ("image/webp", "webp")
    assert sniff(b"<svg xmlns='http://www.w3.org/2000/svg'><script/></svg>") is None
    assert sniff(b"%PDF-1.7") is None


async def test_an_upload_is_stored_under_its_conversation(
    client: AsyncClient, org_headers: dict[str, str], store: dict[str, Any]
) -> None:
    cid = await _conversation(client, org_headers)
    r = await _upload(client, org_headers, cid, JPEG, claimed="image/png")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["content_type"] == "image/jpeg", "the bytes decide, not the claim"
    assert body["bytes"] == len(JPEG) and body["message_id"] is None
    row = await _attachment(body["id"])
    assert row.object_key == f"attachments/{row.org_id}/{cid}/{row.id}.jpg"
    assert store["objects"][row.object_key] == JPEG


async def test_what_is_not_an_image_is_refused(
    client: AsyncClient, org_headers: dict[str, str], store: dict[str, Any]
) -> None:
    cid = await _conversation(client, org_headers)
    r = await _upload(client, org_headers, cid, b"<html><script>x</script></html>", "image/png")
    assert r.status_code == 400 and r.json()["error"]["code"] == "unsupported_image_type"
    assert store["objects"] == {}


async def test_an_image_over_five_megabytes_is_refused(
    client: AsyncClient, org_headers: dict[str, str], store: dict[str, Any]
) -> None:
    cid = await _conversation(client, org_headers)
    r = await _upload(client, org_headers, cid, PNG + b"\x00" * MAX_BYTES)
    assert r.status_code == 400 and r.json()["error"]["code"] == "image_too_large"
    assert store["objects"] == {}


async def test_only_the_starter_can_attach(
    client: AsyncClient, org_headers: dict[str, str], store: dict[str, Any]
) -> None:
    cid = await _conversation(client, org_headers)
    outsider = await _register(client, "outsider@example.com")
    r = await _upload(client, outsider.headers, cid)
    assert r.status_code in (403, 404)
    assert store["objects"] == {}


# ── sending ──────────────────────────────────────────────────


async def test_a_screenshot_is_sent_with_the_message_and_reaches_the_model(
    client: AsyncClient, org_headers: dict[str, str], store: dict[str, Any]
) -> None:
    cid = await _conversation(client, org_headers)
    aid = (await _upload(client, org_headers, cid)).json()["id"]
    r = await client.post(
        f"{API}/conversations/{cid}/messages",
        json={"text": "this error came up", "attachment_ids": [aid]},
        headers=org_headers,
    )
    assert r.status_code == 200, r.text
    await _finish()

    asked, answer = await _messages(cid)
    assert asked.blocks == [{"type": "image", "attachment_id": aid}]
    assert (await _attachment(aid)).message_id == asked.id
    # The fake driver says how many images it received.
    assert "[1 image]" in answer.content

    # The page reads it back with the message, and then the image itself.
    detail = (await client.get(f"{API}/conversations/{cid}", headers=org_headers)).json()
    assert detail["messages"][0]["blocks"] == [{"type": "image", "attachment_id": aid}]
    shown = await client.get(
        f"{API}/conversations/{cid}/attachments/{aid}/content", headers=org_headers
    )
    assert shown.status_code == 200, shown.text
    assert shown.content == PNG

    # Sent once: it can't be sent again.
    again = await client.post(
        f"{API}/conversations/{cid}/messages",
        json={"text": "again", "attachment_ids": [aid]},
        headers=org_headers,
    )
    assert again.status_code == 400 and again.json()["error"]["code"] == "attachment_unavailable"


async def test_a_screenshot_alone_is_a_message_and_nothing_at_all_is_not(
    client: AsyncClient, org_headers: dict[str, str], store: dict[str, Any]
) -> None:
    cid = await _conversation(client, org_headers)
    empty = await client.post(
        f"{API}/conversations/{cid}/messages", json={"text": "  "}, headers=org_headers
    )
    assert empty.status_code == 422
    first = (await _upload(client, org_headers, cid)).json()["id"]
    second = (await _upload(client, org_headers, cid, JPEG)).json()["id"]
    r = await client.post(
        f"{API}/conversations/{cid}/messages",
        json={"attachment_ids": [first, second]},
        headers=org_headers,
    )
    assert r.status_code == 200, r.text
    await _finish()
    asked, answer = await _messages(cid)
    assert asked.content == ""
    assert [b["attachment_id"] for b in asked.blocks] == [first, second], "in the order attached"
    assert "[2 images]" in answer.content


async def test_another_conversations_screenshot_is_refused(
    client: AsyncClient, org_headers: dict[str, str], store: dict[str, Any]
) -> None:
    mine = await _conversation(client, org_headers)
    other = await _conversation(client, org_headers)
    aid = (await _upload(client, org_headers, other)).json()["id"]
    r = await client.post(
        f"{API}/conversations/{mine}/messages",
        json={"text": "look", "attachment_ids": [aid]},
        headers=org_headers,
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "attachment_unavailable"
    assert await _messages(mine) == [], "refused before anything was kept"
    assert (await _attachment(aid)).message_id is None
    # Nor can it be read through the wrong conversation.
    shown = await client.get(
        f"{API}/conversations/{mine}/attachments/{aid}/content", headers=org_headers
    )
    assert shown.status_code == 404


async def test_at_most_four_screenshots_a_message(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    cid = await _conversation(client, org_headers)
    r = await client.post(
        f"{API}/conversations/{cid}/messages",
        json={"text": "many", "attachment_ids": [str(uuid.uuid4()) for _ in range(5)]},
        headers=org_headers,
    )
    assert r.status_code == 422


# ── showing ──────────────────────────────────────────────────


async def test_the_api_serves_the_image_as_an_image_and_nothing_else(
    client: AsyncClient, org_headers: dict[str, str], store: dict[str, Any]
) -> None:
    cid = await _conversation(client, org_headers)
    aid = (await _upload(client, org_headers, cid, JPEG, claimed="image/png")).json()["id"]
    r = await client.get(
        f"{API}/conversations/{cid}/attachments/{aid}/content", headers=org_headers
    )
    assert r.status_code == 200, r.text
    assert r.content == JPEG
    assert r.headers["content-type"] == "image/jpeg", "the type decided from the bytes"
    assert r.headers["content-disposition"] == "inline"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "private, max-age=300"


async def test_another_conversations_image_is_not_found_through_this_one(
    client: AsyncClient, org_headers: dict[str, str], store: dict[str, Any]
) -> None:
    mine = await _conversation(client, org_headers)
    other = await _conversation(client, org_headers)
    aid = (await _upload(client, org_headers, other)).json()["id"]
    r = await client.get(
        f"{API}/conversations/{mine}/attachments/{aid}/content", headers=org_headers
    )
    assert r.status_code == 404 and r.json()["error"]["code"] == "attachment_not_found"
    missing = await client.get(
        f"{API}/conversations/{mine}/attachments/{uuid.uuid4()}/content", headers=org_headers
    )
    assert missing.status_code == 404


async def test_an_outsider_cannot_see_the_image(
    client: AsyncClient, org_headers: dict[str, str], store: dict[str, Any]
) -> None:
    cid = await _conversation(client, org_headers)
    aid = (await _upload(client, org_headers, cid)).json()["id"]
    outsider = await _register(client, "peeker@example.com")
    r = await client.get(
        f"{API}/conversations/{cid}/attachments/{aid}/content", headers=outsider.headers
    )
    assert r.status_code == 404
    assert PNG not in r.content


async def test_an_image_storage_cannot_read_is_a_retry_not_a_not_found(
    client: AsyncClient, org_headers: dict[str, str], store: dict[str, Any]
) -> None:
    cid = await _conversation(client, org_headers)
    aid = (await _upload(client, org_headers, cid)).json()["id"]
    store["objects"].clear()  # the store lost it, or is down
    r = await client.get(
        f"{API}/conversations/{cid}/attachments/{aid}/content", headers=org_headers
    )
    assert r.status_code == 503 and r.json()["error"]["code"] == "attachment_unreadable"


# ── unsent ───────────────────────────────────────────────────


async def test_screenshots_never_sent_are_deleted_after_a_day(
    client: AsyncClient, org_headers: dict[str, str], store: dict[str, Any]
) -> None:
    cid = await _conversation(client, org_headers)
    sent = (await _upload(client, org_headers, cid)).json()["id"]
    stale = (await _upload(client, org_headers, cid, JPEG)).json()["id"]
    fresh = (await _upload(client, org_headers, cid)).json()["id"]
    r = await client.post(
        f"{API}/conversations/{cid}/messages",
        json={"text": "see this", "attachment_ids": [sent]},
        headers=org_headers,
    )
    assert r.status_code == 200, r.text
    await _finish()
    stale_key = (await _attachment(stale)).object_key
    # The sent one and the stale one are two days old; the fresh one is new.
    old = datetime.now(UTC) - timedelta(days=2)
    async with get_sessionmaker()() as s:
        for aid in (sent, stale):
            row = await s.get(Attachment, uuid.UUID(aid))
            assert row is not None
            row.created_at = old
        await s.commit()

    async with get_sessionmaker()() as s:
        assert await sweep_unsent(s) == 1
    assert store["deleted"] == [stale_key]
    async with get_sessionmaker()() as s:
        left = {
            str(i)
            for i in (
                await s.scalars(
                    select(Attachment.id).where(Attachment.conversation_id == uuid.UUID(cid))
                )
            ).all()
        }
    assert left == {sent, fresh}, "a sent one stays, and so does one attached today"

    # Nothing left to do: a second run removes nothing.
    async with get_sessionmaker()() as s:
        assert await sweep_unsent(s) == 0


# ── erasing ──────────────────────────────────────────────────


async def test_erasing_a_conversation_deletes_its_screenshots(
    client: AsyncClient, org_headers: dict[str, str], store: dict[str, Any]
) -> None:
    cid = await _conversation(client, org_headers)
    sent = (await _upload(client, org_headers, cid)).json()["id"]
    unsent = (await _upload(client, org_headers, cid, JPEG)).json()["id"]
    r = await client.post(
        f"{API}/conversations/{cid}/messages",
        json={"text": "see this", "attachment_ids": [sent]},
        headers=org_headers,
    )
    assert r.status_code == 200, r.text
    await _finish()
    keys = {(await _attachment(sent)).object_key, (await _attachment(unsent)).object_key}

    async with get_sessionmaker()() as s:
        conv = await s.get(Conversation, uuid.UUID(cid))
        assert conv is not None
        gone = await retention.purge(s, conv.org_id, [conv.id])
    assert gone == [uuid.UUID(cid)]
    assert set(store["deleted"]) == keys
    async with get_sessionmaker()() as s:
        left = (
            await s.scalars(select(Attachment).where(Attachment.conversation_id == uuid.UUID(cid)))
        ).all()
    assert left == []
