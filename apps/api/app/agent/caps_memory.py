"""The memory tool (task 5.2, plan §4.7): notes that outlive a conversation.

With `memory.memory_tool` on, the model gets one more platform tool,
`memory`, with the interface of Anthropic's memory tool (`memory_20250818`):
a small directory of text files under `/memories` it can `view`, `create`,
`str_replace`, `insert` into, `delete` and `rename`. It uses it to keep what
is worth knowing next time: the user's preferences, facts about their
account, where a piece of work stands.

Built as a platform tool rather than the API's own memory tool type: the
agent runs through the Claude CLI, which can't be handed an API tool
definition, and the client side of that tool (where the files live) is
ours to implement either way. Same commands, same paths, same replies.

**Scope** (`MemoryScope`): per assistant *and* per person. An assistant's
memory of one user must never surface in another user's conversation, so
the owner is the embedding app's user (`external_user_ref`) when there is
one, else the signed-in user who started the conversation, else just the
conversation itself.

**Limits:** paths under `/memories` only, no `..`; at most `MAX_FILES` files
of `MAX_FILE_CHARS` each. Secret-shaped strings are stripped before a file
is stored: a token pasted into chat must not become a permanent note.

**Trust:** a memory file is something the model wrote earlier, possibly
while reading a web page or a document. The system prompt says to treat it
as notes, not instructions, and every file is visible to (and deletable
by) its owner through the API.
"""

from __future__ import annotations

import posixpath
import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.caps import CapabilityTool, _err, _text
from app.db.session import get_sessionmaker
from app.models.memory import MemoryFile
from app.security.redact import strip_secrets

ROOT = "/memories"
MAX_FILES = 100
MAX_FILE_CHARS = 20_000
MAX_PATH_CHARS = 200
_SAFE_PATH = re.compile(r"^/memories(/[A-Za-z0-9._ -]+)*$")


@dataclass(frozen=True)
class MemoryScope:
    org_id: uuid.UUID
    assistant_id: uuid.UUID
    owner_key: str


def owner_key(
    *,
    external_user_ref: str | None,
    user_id: uuid.UUID | None,
    conversation_id: uuid.UUID,
) -> str:
    """Whose memory a conversation reads and writes."""
    if external_user_ref:
        return f"ext:{external_user_ref}"
    if user_id is not None:
        return f"user:{user_id}"
    return f"conv:{conversation_id}"


class MemoryRefused(ValueError):
    """A refused command; its message goes back to the model."""


def normalize_path(raw: object) -> str:
    """`/memories/...`, normalised, or MemoryRefused."""
    if not isinstance(raw, str) or not raw.strip():
        raise MemoryRefused("path is required, e.g. /memories/notes.md")
    path = raw.strip()
    if "\\" in path or "\x00" in path or ".." in path.split("/"):
        raise MemoryRefused(f"{raw!r} is not a valid memory path")
    path = posixpath.normpath(path)
    if len(path) > MAX_PATH_CHARS or not _SAFE_PATH.fullmatch(path):
        raise MemoryRefused(
            f"{raw!r} is not a valid memory path: use /memories/<name>, with letters, "
            "digits, spaces, dots, dashes and underscores"
        )
    return path


def _file_path(raw: object) -> str:
    path = normalize_path(raw)
    if path == ROOT:
        raise MemoryRefused(f"{ROOT} is the directory; name a file inside it")
    return path


def _numbered(content: str, view_range: object) -> str:
    """The file with line numbers, like the memory tool's `view`;
    `view_range` is [first, last], last -1 meaning the end."""
    lines = content.split("\n")
    start, end = 1, len(lines)
    if view_range is not None:
        try:
            first, last = (int(v) for v in view_range)  # type: ignore[attr-defined]
        except (TypeError, ValueError):
            raise MemoryRefused("view_range must be two line numbers, e.g. [1, 20]") from None
        start = max(1, first)
        end = len(lines) if last == -1 else min(len(lines), last)
    return "\n".join(f"{n:6}\t{lines[n - 1]}" for n in range(start, end + 1))


class MemoryStore:
    """The files of one scope. Every query names the scope explicitly."""

    def __init__(self, session: AsyncSession, scope: MemoryScope) -> None:
        self.s = session
        self.scope = scope

    def _where(self) -> Any:
        return (
            (MemoryFile.org_id == self.scope.org_id)
            & (MemoryFile.assistant_id == self.scope.assistant_id)
            & (MemoryFile.owner_key == self.scope.owner_key)
        )

    async def get(self, path: str) -> MemoryFile | None:
        return await self.s.scalar(select(MemoryFile).where(self._where(), MemoryFile.path == path))

    async def under(self, path: str) -> list[MemoryFile]:
        prefix = "" if path == ROOT else path
        rows = await self.s.scalars(
            select(MemoryFile)
            .where(self._where(), MemoryFile.path.startswith(prefix + "/", autoescape=True))
            .order_by(MemoryFile.path)
        )
        return list(rows.all())

    async def count(self) -> int:
        return int(await self.s.scalar(select(func.count()).where(self._where())) or 0)

    async def write(self, path: str, content: str) -> None:
        content = strip_secrets(content)
        if len(content) > MAX_FILE_CHARS:
            raise MemoryRefused(
                f"{path} would be {len(content)} characters; the limit is {MAX_FILE_CHARS}. "
                "Keep memory files short: summarize or split them."
            )
        row = await self.get(path)
        if row is None:
            if await self.count() >= MAX_FILES:
                raise MemoryRefused(f"memory is full ({MAX_FILES} files); delete or merge some")
            if await self.under(path):
                raise MemoryRefused(f"{path} is a directory")
            self.s.add(
                MemoryFile(
                    org_id=self.scope.org_id,
                    assistant_id=self.scope.assistant_id,
                    owner_key=self.scope.owner_key,
                    path=path,
                    content=content,
                )
            )
        else:
            row.content = content


async def _view(store: MemoryStore, args: dict[str, Any]) -> str:
    path = normalize_path(args.get("path"))
    row = None if path == ROOT else await store.get(path)
    if row is not None:
        return f"Here's the content of {path} with line numbers:\n" + _numbered(
            row.content, args.get("view_range")
        )
    files = await store.under(path)
    if not files and path != ROOT:
        raise MemoryRefused(f"{path} does not exist")
    listing = "\n".join(f"- {f.path} ({len(f.content)} characters)" for f in files)
    return f"Here are the files in {path}:\n" + (listing or "(empty)")


async def _create(store: MemoryStore, args: dict[str, Any]) -> str:
    path = _file_path(args.get("path"))
    await store.write(path, str(args.get("file_text") or ""))
    return f"File created successfully at: {path}"


async def _str_replace(store: MemoryStore, args: dict[str, Any]) -> str:
    path = _file_path(args.get("path"))
    row = await store.get(path)
    if row is None:
        raise MemoryRefused(f"{path} does not exist")
    old, new = str(args.get("old_str") or ""), str(args.get("new_str") or "")
    found = row.content.count(old) if old else 0
    if found != 1:
        raise MemoryRefused(f"old_str must appear exactly once in {path}; it appears {found} times")
    await store.write(path, row.content.replace(old, new))
    return f"The memory file {path} has been edited."


async def _insert(store: MemoryStore, args: dict[str, Any]) -> str:
    path = _file_path(args.get("path"))
    row = await store.get(path)
    if row is None:
        raise MemoryRefused(f"{path} does not exist")
    lines = row.content.split("\n") if row.content else []
    try:
        at = int(args.get("insert_line", len(lines)))
    except (TypeError, ValueError):
        raise MemoryRefused("insert_line must be a line number") from None
    if not 0 <= at <= len(lines):
        raise MemoryRefused(f"insert_line must be between 0 and {len(lines)}")
    lines[at:at] = str(args.get("insert_text") or "").split("\n")
    await store.write(path, "\n".join(lines))
    return f"The memory file {path} has been edited."


async def _delete(store: MemoryStore, args: dict[str, Any]) -> str:
    path = normalize_path(args.get("path"))
    if path == ROOT:
        raise MemoryRefused(f"{ROOT} itself can't be deleted; delete the files in it")
    row = await store.get(path)
    doomed = [row] if row is not None else await store.under(path)
    if not doomed:
        raise MemoryRefused(f"{path} does not exist")
    for f in doomed:
        await store.s.delete(f)
    return f"Deleted {path}"


async def _rename(store: MemoryStore, args: dict[str, Any]) -> str:
    old, new = _file_path(args.get("old_path")), _file_path(args.get("new_path"))
    row = await store.get(old)
    if row is None:
        raise MemoryRefused(f"{old} does not exist")
    if await store.get(new) is not None:
        raise MemoryRefused(f"{new} already exists")
    row.path = new
    return f"Renamed {old} to {new}"


_COMMANDS = {
    "view": _view,
    "create": _create,
    "str_replace": _str_replace,
    "insert": _insert,
    "delete": _delete,
    "rename": _rename,
}

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "command": {"type": "string", "enum": sorted(_COMMANDS)},
        "path": {"type": "string", "description": "Under /memories, e.g. /memories/notes.md"},
        "view_range": {"type": "array", "items": {"type": "integer"}},
        "file_text": {"type": "string"},
        "old_str": {"type": "string"},
        "new_str": {"type": "string"},
        "insert_line": {"type": "integer"},
        "insert_text": {"type": "string"},
        "old_path": {"type": "string"},
        "new_path": {"type": "string"},
    },
    "required": ["command"],
}

_DESCRIPTION = (
    "Your persistent memory: text files under /memories that you keep for the next "
    "conversation with this same user. Commands: view (a file, or a directory "
    "listing), create (write a whole file), str_replace, insert, delete, rename. "
    "Store durable facts, preferences and progress, not the conversation itself, "
    "and never passwords or keys."
)


def build_memory_tool(scope: MemoryScope) -> CapabilityTool:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        command = _COMMANDS.get(str(args.get("command", "")))
        if command is None:
            return _err(f"unknown command; use one of: {', '.join(sorted(_COMMANDS))}")
        async with get_sessionmaker()() as session:
            try:
                reply = await command(MemoryStore(session, scope), args)
            except MemoryRefused as exc:
                await session.rollback()
                return _err(str(exc))
            await session.commit()
        return _text(reply)

    return CapabilityTool(
        name="memory",
        description=_DESCRIPTION,
        input_schema=_SCHEMA,
        handler=handler,
        read_only=False,
    )


__all__ = [
    "MAX_FILES",
    "MAX_FILE_CHARS",
    "ROOT",
    "MemoryRefused",
    "MemoryScope",
    "MemoryStore",
    "build_memory_tool",
    "normalize_path",
    "owner_key",
]
