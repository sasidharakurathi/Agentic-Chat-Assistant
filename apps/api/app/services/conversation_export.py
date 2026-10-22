"""A conversation as a file to keep (Phase 10.5): Markdown to read, or JSON
for another tool.

- **Who**: whoever may read the conversation (its starter, or an admin
  with the recorded reason), through the same dependency as opening it,
  so an admin's export is a recorded read like any other.
- **What**: the messages and their sources, as the reader sees them in
  Chat. The same shaping applies (`services/chat_view`): an employee gets
  no tool inputs or outputs, no model or tokens, and a request's first line
  only; citations of sources the reader may no longer read stay out
  (9.4). Markdown names the tools used, in words, for everyone; JSON
  carries a builder's or admin's tool details, as the trace would.
- **Recorded**: `conversation.export`, with the format and how many
  messages, never the content or the title.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assistant import Assistant
from app.models.conversation import Conversation, Message, MessageRole
from app.models.membership import Membership
from app.schemas.conversation import MessageOut
from app.services import chat_view, knowledge_access

#: The most messages one export holds: a very long conversation, bounded.
EXPORT_LIMIT = 5000

_UNSAFE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Export:
    content: str
    media_type: str
    filename: str
    messages: int


def _when(at: datetime) -> str:
    aware = at if at.tzinfo else at.replace(tzinfo=UTC)
    return aware.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _filename(title: str, now: datetime, ext: str) -> str:
    """ASCII only, so every browser saves it under this name."""
    base = _UNSAFE.sub("-", title.lower()).strip("-")[:60].strip("-") or "conversation"
    return f"{base}-{now:%Y-%m-%d}.{ext}"


def _citations(blocks: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[Any] = set()
    for b in blocks:
        if not isinstance(b, dict) or b.get("type") != "citation":
            continue
        marker = b.get("marker")
        if marker in seen:
            continue
        seen.add(marker)
        out.append(
            {
                "marker": marker,
                "title": str(b.get("title") or "Untitled source"),
                "link": b.get("href") or b.get("uri"),
                "snippet": str(b.get("snippet") or ""),
            }
        )
    return sorted(out, key=lambda c: (c["marker"] is None, c["marker"] or 0))


def _tools(blocks: list[Any], *, shaped: bool) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for b in blocks:
        if not isinstance(b, dict) or b.get("type", "tool_call") != "tool_call":
            continue
        name = str(b.get("name") or "")
        if shaped:
            # Already a label (chat_view.message); nothing a tool returned.
            out.append({"name": name, "status": b.get("status")})
        else:
            out.append(
                {
                    "name": name,
                    "label": chat_view.tool_label(name),
                    "status": b.get("status"),
                    "input": b.get("input") or {},
                    "output": b.get("output"),
                }
            )
    return out


async def build(
    session: AsyncSession,
    *,
    conv: Conversation,
    assistant: Assistant,
    membership: Membership,
    fmt: str,
) -> Export:
    """The file, shaped for this reader. `fmt` is "md" or "json"."""
    rows = list(
        (
            await session.scalars(
                select(Message)
                .where(
                    Message.conversation_id == conv.id,
                    Message.role.in_([MessageRole.user, MessageRole.assistant, MessageRole.system]),
                )
                .order_by(Message.created_at, Message.id)
                .limit(EXPORT_LIMIT)
            )
        ).all()
    )
    shaped = chat_view.shaped(membership)
    allowed = await knowledge_access.readable_ids(session, assistant, membership)
    lost = await knowledge_access.lost_any(session, assistant.id, allowed)
    messages: list[MessageOut] = []
    for row in rows:
        out = MessageOut.model_validate(row).model_copy(
            update=knowledge_access.shown(row, allowed, lost)
        )
        messages.append(chat_view.message(out) if shaped else out)
    now = datetime.now(UTC)
    title = conv.title or "Conversation"
    if fmt == "json":
        payload = {
            "conversation": {
                "id": str(conv.id),
                "title": title,
                "assistant": assistant.name,
                "started_at": conv.created_at.isoformat() if conv.created_at else None,
                "exported_at": now.isoformat(),
            },
            "messages": [
                {
                    "id": str(m.id),
                    "role": m.role.value,
                    "content": m.content,
                    "created_at": m.created_at.isoformat(),
                    "citations": _citations(m.blocks),
                    "tools": _tools(m.blocks, shaped=shaped),
                }
                for m in messages
            ],
        }
        return Export(
            content=json.dumps(payload, ensure_ascii=False, indent=2),
            media_type="application/json; charset=utf-8",
            filename=_filename(title, now, "json"),
            messages=len(messages),
        )

    own = conv.created_by == membership.user_id
    asker = "You" if own else "The person who asked"
    lines = [
        f"# {title}",
        "",
        f"{assistant.name}. Exported {_when(now)}.",
        "",
    ]
    for m in messages:
        who = {
            MessageRole.user: asker,
            MessageRole.assistant: assistant.name,
        }.get(m.role, "Note")
        lines += ["---", "", f"**{who}**, {_when(m.created_at)}", "", m.content.strip(), ""]
        # Tools in words, for everyone: what they returned is in the JSON.
        used = sorted({str(t.get("label", t["name"])) for t in _tools(m.blocks, shaped=shaped)})
        if used:
            lines += [f"_Used: {', '.join(used)}_", ""]
        sources = _citations(m.blocks)
        if sources:
            lines.append("Sources:")
            for c in sources:
                label = f"[{c['marker']}] " if c["marker"] is not None else ""
                link = f" ({c['link']})" if c["link"] else ""
                lines.append(f"- {label}{c['title']}{link}")
            lines.append("")
    return Export(
        content="\n".join(lines).rstrip() + "\n",
        media_type="text/markdown; charset=utf-8",
        filename=_filename(title, now, "md"),
        messages=len(messages),
    )


__all__ = ["EXPORT_LIMIT", "Export", "build"]
