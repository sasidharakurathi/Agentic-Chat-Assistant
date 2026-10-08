"""The files the Claude Code CLI writes, and how they go away (Phase 7a.5).

Each turn runs the CLI in a working directory of its own conversation
(`scratch/<conversation id>`), and the CLI writes the whole session to a
JSONL transcript (prompts, tool input and output including SQL rows,
answers) under its config directory, `CLAUDE_CONFIG_DIR`:

    <agent_state_dir>/claude/projects/<working dir, non-alphanumerics as "-">/<session id>.jsonl

The next turn resumes from that file. Postgres stays the source of truth:
a missing transcript means the turn replays the conversation from its
stored messages instead (`has_transcript`). So these files are a cache, kept
on a volume that survives a redeploy and out of backups on purpose.

They are also personal data, so they are removed with their conversation:
on archive and on assistant delete (`discard`), and by a daily sweep
(`sweep`) that clears what those missed.

The CLI's folder name ends with the conversation id, because the working
directory does, so a conversation's transcripts are found by that suffix.
The CLI shortens folder names over 200 characters, which is why
`agent_state_dir` must stay short.
"""

from __future__ import annotations

import re
import shutil
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.logging import get_logger

log = get_logger(__name__)

#: A conversation id at the end of a folder name.
_TRAILING_ID = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")
#: What a CLI session id looks like; anything else is never used in a path.
_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
#: A transcript this recent is left alone by the sweep even if it is not the
#: conversation's current session: a turn may be writing it right now.
SWEEP_MIN_AGE_S = 24 * 3600


def _root() -> Path:
    return Path(settings.agent_state_dir)


def config_dir() -> Path:
    """The CLI's `CLAUDE_CONFIG_DIR`."""
    d = _root() / "claude"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _scratch_base() -> Path:
    return _root() / "scratch"


def scratch_dir(conversation_id: uuid.UUID) -> Path:
    """The conversation's working directory, created if needed."""
    d = _scratch_base() / str(conversation_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _projects() -> Path:
    return _root() / "claude" / "projects"


def _transcript_dirs(conversation_id: uuid.UUID) -> list[Path]:
    projects = _projects()
    if not projects.is_dir():
        return []
    return [d for d in projects.glob(f"*{conversation_id}") if d.is_dir()]


def has_transcript(conversation_id: uuid.UUID, session_id: str) -> bool:
    """Whether the CLI can resume `session_id` for this conversation: its
    transcript is on disk. False after a redeploy that lost the volume, after
    the CLI's own clean-up, or for an id that is not a session id."""
    if not _SESSION_ID.match(session_id):
        return False
    return any((d / f"{session_id}.jsonl").is_file() for d in _transcript_dirs(conversation_id))


def discard(conversation_id: uuid.UUID) -> None:
    """Remove everything the CLI kept for a conversation: its working
    directory and every transcript of it."""
    shutil.rmtree(_scratch_base() / str(conversation_id), ignore_errors=True)
    for d in _transcript_dirs(conversation_id):
        shutil.rmtree(d, ignore_errors=True)


def conversation_ids_on_disk() -> set[uuid.UUID]:
    """Every conversation with a working directory or a transcript folder."""
    ids: set[uuid.UUID] = set()
    for base in (_scratch_base(), _projects()):
        if not base.is_dir():
            continue
        for d in base.iterdir():
            m = _TRAILING_ID.search(d.name)
            if d.is_dir() and m:
                ids.add(uuid.UUID(m.group(1)))
    return ids


@dataclass
class SweepResult:
    #: Conversations whose files were all removed (gone or archived).
    conversations: int = 0
    #: Old transcripts of live conversations that will never be resumed.
    transcripts: int = 0


def sweep(keep: Mapping[uuid.UUID, str | None], *, now: float | None = None) -> SweepResult:
    """Remove what no conversation needs.

    `keep` maps each live conversation found on disk to the session it would
    resume (its `sdk_session_id`, or None). A conversation not in `keep` is
    deleted or archived: all its files go. For a live one, transcripts other
    than its current session go once they are a day old (a new session
    starts after a summary, or on every message when history is off, and
    the old ones are never read again). Folders without a conversation id
    are not ours and are left alone."""
    now = time.time() if now is None else now
    result = SweepResult()
    for conversation_id in conversation_ids_on_disk():
        if conversation_id not in keep:
            discard(conversation_id)
            result.conversations += 1
            continue
        current = keep[conversation_id]
        for d in _transcript_dirs(conversation_id):
            for f in d.glob("*.jsonl"):
                if f.stem == current or now - f.stat().st_mtime < SWEEP_MIN_AGE_S:
                    continue
                f.unlink(missing_ok=True)
                # The CLI keeps a session's subagent transcripts beside it.
                shutil.rmtree(d / f.stem, ignore_errors=True)
                result.transcripts += 1
    log.info(
        "agent_files_sweep",
        conversations=result.conversations,
        transcripts=result.transcripts,
    )
    return result


__all__ = [
    "SWEEP_MIN_AGE_S",
    "SweepResult",
    "config_dir",
    "conversation_ids_on_disk",
    "discard",
    "has_transcript",
    "scratch_dir",
    "sweep",
]
