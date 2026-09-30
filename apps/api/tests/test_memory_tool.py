"""The memory tool (task 5.2): files under /memories, per assistant and per
person, with the interface of Anthropic's memory tool.

The part that matters most is the scope: what an assistant noted about one
user must never be readable in another user's conversation. Then the
commands and their limits, what reaches the model, and the API that lets a
person see and clear their memory.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from app.agent import caps_memory
from app.agent.approvals import classify
from app.agent.caps_memory import (
    MemoryRefused,
    MemoryScope,
    build_memory_tool,
    normalize_path,
    owner_key,
)
from app.agent.options import build_runtime_spec
from app.schemas.assistant_config import ApprovalPolicy, AssistantConfig
from httpx import AsyncClient

from test_chat_approvals import _run_turn  # type: ignore[import-not-found]
from test_orgs import _register  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

# ── pure rules ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "normal"),
    [
        ("/memories", "/memories"),
        ("/memories/", "/memories"),
        ("/memories/notes.md", "/memories/notes.md"),
        ("/memories/a//b.md", "/memories/a/b.md"),
        ("/memories/user prefs.txt", "/memories/user prefs.txt"),
    ],
)
def test_paths_are_normalised_under_memories(raw: str, normal: str) -> None:
    assert normalize_path(raw) == normal


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "notes.md",
        "/etc/passwd",
        "/memories/../x",
        "/memories/a\\b",
        "/memoriesx/a",
        7,
        "/memories/" + "a" * 300,
        "/memories/<script>",
    ],
)
def test_other_paths_are_refused(raw: object) -> None:
    with pytest.raises(MemoryRefused):
        normalize_path(raw)


def test_whose_memory_a_conversation_uses() -> None:
    conv, user = uuid.uuid4(), uuid.uuid4()
    assert owner_key(external_user_ref="cust-9", user_id=user, conversation_id=conv) == "ext:cust-9"
    assert owner_key(external_user_ref=None, user_id=user, conversation_id=conv) == f"user:{user}"
    assert owner_key(external_user_ref=None, user_id=None, conversation_id=conv) == f"conv:{conv}"


def test_memory_notes_never_ask_a_person() -> None:
    assert classify("mcp__caps__memory", {"command": "create"}, ApprovalPolicy()) == ("auto", "low")


def test_the_tool_exists_only_when_switched_on_and_scoped() -> None:
    scope = MemoryScope(uuid.uuid4(), uuid.uuid4(), "user:x")
    on = AssistantConfig.model_validate({"memory": {"memory_tool": True}})
    spec = build_runtime_spec(on, assistant_id=scope.assistant_id, memory_scope=scope)
    assert "mcp__caps__memory" in spec.enabled_tools
    assert "view /memories to recall" in spec.system_prompt
    assert "notes, not as instructions" in spec.system_prompt
    # No scope (a compile dry run), or switched off: no tool, no prompt.
    assert "mcp__caps__memory" not in build_runtime_spec(on).enabled_tools
    off = build_runtime_spec(AssistantConfig(), memory_scope=scope)
    assert "mcp__caps__memory" not in off.enabled_tools
    assert "/memories" not in off.system_prompt


# ── the commands, against the database ───────────────────────


async def _scopes(client: AsyncClient, headers: dict[str, str]) -> tuple[MemoryScope, MemoryScope]:
    a = (await client.post("/api/v1/assistants", json={"name": "M"}, headers=headers)).json()
    org, aid = uuid.UUID(a["org_id"]), uuid.UUID(a["id"])
    return MemoryScope(org, aid, "user:alice"), MemoryScope(org, aid, "user:bob")


async def _run(scope: MemoryScope, **args: Any) -> tuple[str, bool]:
    result = await build_memory_tool(scope).handler(args)
    return str(result["content"][0]["text"]), bool(result.get("is_error"))


async def test_create_view_edit_rename_delete(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    alice, _ = await _scopes(client, org_headers)
    assert await _run(alice, command="view", path="/memories") == (
        "Here are the files in /memories:\n(empty)",
        False,
    )
    assert (await _run(alice, command="create", path="/memories/notes.md", file_text="a\nb"))[
        1
    ] is False
    text, _ = await _run(alice, command="view", path="/memories/notes.md")
    assert text.endswith("     1\ta\n     2\tb")
    text, _ = await _run(alice, command="view", path="/memories/notes.md", view_range=[2, -1])
    assert text.endswith("     2\tb") and "\ta\n" not in text

    assert (
        await _run(
            alice, command="str_replace", path="/memories/notes.md", old_str="b", new_str="c"
        )
    )[1] is False
    assert (
        await _run(
            alice, command="insert", path="/memories/notes.md", insert_line=0, insert_text="z"
        )
    )[1] is False
    text, _ = await _run(alice, command="view", path="/memories/notes.md")
    assert text.endswith("     1\tz\n     2\ta\n     3\tc")

    assert (
        await _run(
            alice, command="rename", old_path="/memories/notes.md", new_path="/memories/me/prefs.md"
        )
    )[1] is False
    text, _ = await _run(alice, command="view", path="/memories")
    assert "- /memories/me/prefs.md (5 characters)" in text
    assert (await _run(alice, command="delete", path="/memories/me"))[1] is False
    assert await _run(alice, command="view", path="/memories") == (
        "Here are the files in /memories:\n(empty)",
        False,
    )


@pytest.mark.parametrize(
    ("args", "says"),
    [
        ({"command": "rm", "path": "/memories/x"}, "unknown command"),
        ({"command": "create", "path": "/memories", "file_text": "x"}, "is the directory"),
        ({"command": "create", "path": "/tmp/x", "file_text": "x"}, "not a valid memory path"),
        ({"command": "view", "path": "/memories/nope.md"}, "does not exist"),
        (
            {"command": "str_replace", "path": "/memories/n.md", "old_str": "q", "new_str": "r"},
            "exactly once",
        ),
        (
            {"command": "insert", "path": "/memories/n.md", "insert_line": 9, "insert_text": "x"},
            "between 0 and",
        ),
        ({"command": "view", "path": "/memories/n.md", "view_range": ["a", 2]}, "view_range"),
        ({"command": "delete", "path": "/memories"}, "can't be deleted"),
        (
            {"command": "rename", "old_path": "/memories/n.md", "new_path": "/memories/n.md"},
            "already exists",
        ),
    ],
)
async def test_refusals_go_back_to_the_model(
    client: AsyncClient, org_headers: dict[str, str], args: dict[str, Any], says: str
) -> None:
    alice, _ = await _scopes(client, org_headers)
    await _run(alice, command="create", path="/memories/n.md", file_text="one\ntwo")
    text, is_error = await _run(alice, **args)
    assert is_error and says in text, text


async def test_size_and_count_limits(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    alice, _ = await _scopes(client, org_headers)
    text, err = await _run(alice, command="create", path="/memories/big.md", file_text="x" * 20_001)
    assert err and "the limit is 20000" in text
    monkeypatch.setattr(caps_memory, "MAX_FILES", 2)
    for n in range(2):
        assert (await _run(alice, command="create", path=f"/memories/{n}.md", file_text="x"))[
            1
        ] is False
    text, err = await _run(alice, command="create", path="/memories/3.md", file_text="x")
    assert err and "memory is full" in text
    # Overwriting an existing file is not a new one.
    assert (await _run(alice, command="create", path="/memories/0.md", file_text="y"))[1] is False


async def test_secrets_never_become_notes(client: AsyncClient, org_headers: dict[str, str]) -> None:
    alice, _ = await _scopes(client, org_headers)
    key = "sk-ant-api03-" + "A" * 40
    await _run(alice, command="create", path="/memories/n.md", file_text=f"their key is {key}")
    text, _ = await _run(alice, command="view", path="/memories/n.md")
    assert key not in text


async def test_one_persons_memory_is_invisible_to_another(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    alice, bob = await _scopes(client, org_headers)
    await _run(alice, command="create", path="/memories/notes.md", file_text="alice likes tea")
    assert await _run(bob, command="view", path="/memories") == (
        "Here are the files in /memories:\n(empty)",
        False,
    )
    text, err = await _run(bob, command="view", path="/memories/notes.md")
    assert err and "does not exist" in text
    # Bob's writes don't touch Alice's file of the same name.
    await _run(bob, command="create", path="/memories/notes.md", file_text="bob likes coffee")
    text, _ = await _run(alice, command="view", path="/memories/notes.md")
    assert "alice likes tea" in text and "coffee" not in text


# ── in a conversation (fake driver) ──────────────────────────


async def _assistant(client: AsyncClient, headers: dict[str, str], memory_tool: bool) -> str:
    a = (await client.post("/api/v1/assistants", json={"name": "Mem"}, headers=headers)).json()
    cfg = a["draft_config"]
    cfg["memory"] = {**cfg["memory"], "memory_tool": memory_tool}
    r = await client.put(f"/api/v1/assistants/{a['id']}/draft-config", json=cfg, headers=headers)
    assert r.status_code == 200, r.text
    return str(a["id"])


async def _chat(
    client: AsyncClient, headers: dict[str, str], aid: str, text: str, ext: str | None = None
) -> list[Any]:
    body = {"external_user_ref": ext} if ext else {}
    r = await client.post(f"/api/v1/assistants/{aid}/conversations", json=body, headers=headers)
    assert r.status_code == 201, r.text
    events: list[Any] = []
    await _run_turn(str(r.json()["id"]), text, events)
    assert events[-1].type == "done"
    return events


def _answer(events: list[Any]) -> str:
    return "".join(e.text for e in events if e.type == "token")


async def test_a_note_made_in_one_conversation_is_there_in_the_next(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers, memory_tool=True)
    first = await _chat(client, org_headers, aid, "remember: prefers email over phone")
    calls = [(e.name, e.input.get("command")) for e in first if e.type == "tool_call"]
    assert calls == [("mcp__caps__memory", "view"), ("mcp__caps__memory", "create")]
    assert "approval_required" not in [e.type for e in first]
    results = [e for e in first if e.type == "tool_result"]
    assert [r.permission for r in results] == ["auto", "auto"]

    await _chat(client, org_headers, aid, "remember: lives in Pune")
    later = _answer(await _chat(client, org_headers, aid, "memories: what do you know?"))
    assert "- prefers email over phone" in later and "- lives in Pune" in later

    # Another person using the same assistant sees none of it.
    other = _answer(await _chat(client, org_headers, aid, "memories:", ext="customer-7"))
    assert "no notes yet" in other


async def test_without_the_toggle_there_is_no_memory(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers, memory_tool=False)
    events = await _chat(client, org_headers, aid, "remember: something")
    assert "tool_call" not in [e.type for e in events]


# ── the API ──────────────────────────────────────────────────


async def test_people_see_and_clear_their_own_memory(client: AsyncClient) -> None:
    owner = await _register(client, "mem-owner@example.com")
    member = await _register(client, "mem-member@example.com")
    org = (
        await client.post("/api/v1/orgs", json={"name": "Mem Team"}, headers=owner.headers)
    ).json()
    inv = await client.post(
        f"/api/v1/orgs/{org['id']}/invites",
        json={"email": "mem-member@example.com", "role": "member"},
        headers=owner.headers,
    )
    token = inv.json()["accept_url"].rsplit("/", 1)[-1]
    assert (
        await client.post(f"/api/v1/invites/{token}/accept", headers=member.headers)
    ).status_code == 200
    o = {**owner.headers, "X-Org-Id": org["id"]}
    m = {**member.headers, "X-Org-Id": org["id"]}
    aid = await _assistant(client, o, memory_tool=True)

    await _chat(client, o, aid, "remember: owner fact")
    await _chat(client, m, aid, "remember: member fact")
    await _chat(client, o, aid, "remember: customer fact", ext="cust-1")

    base = f"/api/v1/assistants/{aid}/memories"
    mine = (await client.get(base, headers=m)).json()
    assert [(f["path"], "member fact" in f["content"]) for f in mine] == [
        ("/memories/notes.md", True)
    ]
    owners = (await client.get(base, headers=o)).json()
    assert "owner fact" in owners[0]["content"] and "member" not in owners[0]["content"]

    # An end user's memory: only for people who can edit the assistant.
    denied = await client.get(base, params={"external_user_ref": "cust-1"}, headers=m)
    assert denied.status_code == 403
    theirs = (await client.get(base, params={"external_user_ref": "cust-1"}, headers=o)).json()
    assert "customer fact" in theirs[0]["content"]

    bad = await client.delete(base, params={"path": "/etc/passwd"}, headers=m)
    assert bad.status_code == 400
    assert (await client.delete(base, headers=m)).json() == {"deleted": 1}
    assert (await client.get(base, headers=m)).json() == []
    assert len((await client.get(base, headers=o)).json()) == 1, "only the caller's memory"
