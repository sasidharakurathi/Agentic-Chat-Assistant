"""The Claude Code CLI's files: one known place, resumed only when present,
and removed with their conversation (Phase 7a.5).

The CLI keeps each session as a JSONL transcript (prompts, tool output
including SQL rows, answers) under `CLAUDE_CONFIG_DIR`. They used to land in
the server user's home: lost on every redeploy, which broke every resumed
conversation for good, and never deleted, not even with their assistant.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from app import worker
from app.agent import cli_files
from app.agent.options import build_claude_options, build_runtime_spec
from app.config import settings
from app.db.session import get_sessionmaker
from app.models.conversation import Conversation
from app.schemas.assistant_config import AssistantConfig
from app.services import chat, conversation_memory
from httpx import AsyncClient

pytestmark = pytest.mark.anyio

API = "/api/v1"


@pytest.fixture(autouse=True)
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "state"
    monkeypatch.setattr(settings, "agent_state_dir", d)
    return d


def _transcript(conversation_id: uuid.UUID, session_id: str, *, age_s: float = 0) -> Path:
    """A transcript where the CLI writes one: a folder named after the
    working directory, every non-alphanumeric character turned into "-"."""
    cwd = cli_files.scratch_dir(conversation_id)
    folder = "".join(ch if ch.isalnum() else "-" for ch in str(cwd))
    f = cli_files.config_dir() / "projects" / folder / f"{session_id}.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text('{"type":"user"}\n', encoding="utf-8")
    if age_s:
        then = time.time() - age_s
        os.utime(f, (then, then))
    return f


# ── where the files go ───────────────────────────────────────


def test_the_cli_is_given_one_known_config_directory(state_dir: Path) -> None:
    async def allow(*_a: Any, **_k: Any) -> Any:
        return None

    options = build_claude_options(build_runtime_spec(AssistantConfig()), allow)
    assert options.env["CLAUDE_CONFIG_DIR"] == str(state_dir / "claude")
    assert cli_files.scratch_dir(uuid.uuid4()).parent == state_dir / "scratch"


def test_the_production_path_keeps_the_conversation_id_in_the_folder_name() -> None:
    """The CLI shortens folder names over 200 characters, which would cut
    the id off the end and hide the transcript from `discard`."""
    cwd = f"/var/lib/assistant-studio/scratch/{uuid.uuid4()}"
    assert len("".join(ch if ch.isalnum() else "-" for ch in cwd)) <= 200


# ── resume only what is there ────────────────────────────────


def test_a_transcript_is_found_by_its_conversation_and_session() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    _transcript(a, "sess-1")
    assert cli_files.has_transcript(a, "sess-1")
    assert not cli_files.has_transcript(a, "sess-2")
    assert not cli_files.has_transcript(b, "sess-1"), "another conversation's session"


def test_an_id_that_is_not_a_session_id_is_never_used_as_a_path() -> None:
    a = uuid.uuid4()
    _transcript(a, "sess-1")
    for bad in ("../sess-1", "sess-1/../sess-1", "*", ""):
        assert not cli_files.has_transcript(a, bad), bad


async def test_a_missing_transcript_is_replayed_instead_of_resumed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conv = SimpleNamespace(id=uuid.uuid4(), sdk_session_id="sess-gone")
    monkeypatch.setattr(chat, "get_driver", lambda: SimpleNamespace(name="claude"))
    assert await chat._session_to_resume(conv) is None  # type: ignore[arg-type]
    _transcript(conv.id, "sess-gone")
    assert await chat._session_to_resume(conv) == "sess-gone"  # type: ignore[arg-type]


async def test_the_fake_driver_resumes_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    """It keeps no files, so there is nothing to look for."""
    conv = SimpleNamespace(id=uuid.uuid4(), sdk_session_id="fake-abc")
    assert await chat._session_to_resume(conv) == "fake-abc"  # type: ignore[arg-type]


async def test_a_turn_after_a_lost_transcript_replays_the_stored_messages(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end: the conversation row still names a session, the file is
    gone (a redeploy), and the next turn starts fresh from the database."""
    aid = (await client.post(f"{API}/assistants", json={"name": "A"}, headers=org_headers)).json()[
        "id"
    ]
    await client.post(f"{API}/assistants/{aid}/versions", json={"note": "v1"}, headers=org_headers)
    cid = (
        await client.post(f"{API}/assistants/{aid}/conversations", json={}, headers=org_headers)
    ).json()["id"]
    first = await client.post(
        f"{API}/conversations/{cid}/messages", json={"text": "hello"}, headers=org_headers
    )
    assert first.status_code == 200
    async with get_sessionmaker()() as s:
        row = await s.get(Conversation, uuid.UUID(cid))
        assert row is not None and row.sdk_session_id, "the first turn recorded a session"

    seen: list[str | None] = []
    plan_turn = conversation_memory.plan_turn

    async def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs["resume_id"])
        return await plan_turn(*args, **kwargs)

    monkeypatch.setattr(conversation_memory, "plan_turn", spy)
    monkeypatch.setattr(chat, "get_driver", lambda: SimpleNamespace(name="claude"))
    again = await client.post(
        f"{API}/conversations/{cid}/messages", json={"text": "and again"}, headers=org_headers
    )
    assert again.status_code == 200
    assert seen == [None], "nothing to resume, so the stored messages are replayed"


# ── removed with their conversation ──────────────────────────


def test_discard_removes_one_conversations_files_and_no_others(state_dir: Path) -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    ta, tb = _transcript(a, "s1"), _transcript(b, "s2")
    cli_files.discard(a)
    assert not ta.parent.exists(), "the transcript folder"
    assert not (state_dir / "scratch" / str(a)).exists(), "the working directory"
    assert tb.exists() and (state_dir / "scratch" / str(b)).is_dir(), "the other is untouched"


async def test_archiving_a_conversation_removes_its_cli_files(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = (await client.post(f"{API}/assistants", json={"name": "A"}, headers=org_headers)).json()[
        "id"
    ]
    cid = (
        await client.post(f"{API}/assistants/{aid}/conversations", json={}, headers=org_headers)
    ).json()["id"]
    t = _transcript(uuid.UUID(cid), "s1")
    scratch = t.parents[3] / "scratch" / cid
    assert scratch.is_dir()
    assert (await client.delete(f"{API}/conversations/{cid}", headers=org_headers)).is_success
    assert not t.exists() and not scratch.exists()


async def test_deleting_an_assistant_removes_its_conversations_cli_files(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = (await client.post(f"{API}/assistants", json={"name": "A"}, headers=org_headers)).json()[
        "id"
    ]
    cid = (
        await client.post(f"{API}/assistants/{aid}/conversations", json={}, headers=org_headers)
    ).json()["id"]
    t = _transcript(uuid.UUID(cid), "s1")
    assert (await client.delete(f"{API}/assistants/{aid}", headers=org_headers)).is_success
    assert not t.exists()


# ── the daily sweep ──────────────────────────────────────────


def test_the_sweep_keeps_what_live_conversations_resume_and_nothing_else(
    state_dir: Path,
) -> None:
    live, gone = uuid.uuid4(), uuid.uuid4()
    day = cli_files.SWEEP_MIN_AGE_S
    current = _transcript(live, "now", age_s=3 * day)
    replaced = _transcript(live, "old", age_s=3 * day)
    (replaced.parent / "old" / "subagents").mkdir(parents=True)
    writing = _transcript(live, "new-turn")  # a turn may be writing it now
    ghost = _transcript(gone, "s1", age_s=3 * day)
    foreign = cli_files.config_dir() / "projects" / "someone-elses-project"
    foreign.mkdir(parents=True)

    result = cli_files.sweep({live: "now"})

    assert current.exists() and writing.exists()
    assert not replaced.exists() and not (replaced.parent / "old").exists()
    assert not ghost.parent.exists() and not (state_dir / "scratch" / str(gone)).exists()
    assert foreign.is_dir(), "a folder without a conversation id is not ours"
    assert (result.conversations, result.transcripts) == (1, 1)


async def test_the_worker_sweep_clears_archived_and_deleted_conversations(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = (await client.post(f"{API}/assistants", json={"name": "A"}, headers=org_headers)).json()[
        "id"
    ]

    async def conversation() -> uuid.UUID:
        r = await client.post(f"{API}/assistants/{aid}/conversations", json={}, headers=org_headers)
        return uuid.UUID(r.json()["id"])

    live, archived = await conversation(), await conversation()
    kept = _transcript(live, "s1")
    # Archived before this release: its files were never removed.
    async with get_sessionmaker()() as s:
        row = await s.get(Conversation, archived)
        assert row is not None
        await chat.archive(s, row)
    stale = _transcript(archived, "s1")
    ghost = _transcript(uuid.uuid4(), "s1")

    await worker.agent_files_sweep({})

    assert kept.exists()
    assert not stale.exists() and not ghost.exists()


def test_the_sweep_runs_every_day() -> None:
    names = [job.name for job in worker.WorkerSettings.cron_jobs]
    assert any("agent_files_sweep" in n for n in names)
