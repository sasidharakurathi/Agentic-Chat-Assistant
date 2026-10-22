"""The `hand_off` tool (Phase 9.5): the assistant gets a person.

Offered when the assistant's hand-off is on and the turn has someone to
hand off for (a signed-in person in a live or preview chat; never an eval).
The model calls it when it can't help, or when the person asks for a
human, with a short note. What happens is `services.handoffs`: the contact
is emailed a link to the conversation, or a ticket is opened through a
declared `read` action. A `confirm` action needs the person's own click on
"Get help from a person", so from here it falls back to the email.

It runs without asking (`approvals._OWN_REQUESTS`): it tells one person
inside the organisation where to look, and sends no conversation content
anywhere the builder didn't approve with the version.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.agent.caps import CapabilityTool, _err, _text

HANDOFF_TOOL = "hand_off"
#: The longest note the model may write.
NOTE_MAX = 1000

#: What the turn is handed: given the model's note, hand the conversation
#: off and say what happened, or raise `HandoffRefused`.
HandOff = Callable[[str], Awaitable[str]]


class HandoffRefused(Exception):
    """Said back to the model as an error: it can tell the person why."""


def build_handoff_tool(handoff: HandOff) -> CapabilityTool:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        note = str(args.get("note") or "").strip()[:NOTE_MAX]
        try:
            return _text(await handoff(note))
        except HandoffRefused as exc:
            return _err(f"error: {exc}")

    return CapabilityTool(
        name=HANDOFF_TOOL,
        description=(
            "Hand this conversation to a person on the team. Use it when you can't help "
            "with what the person needs, or when they ask for a human. Don't use it for "
            "something you can answer. Write a short note saying what they need and what "
            "you tried. Tell the person plainly what happens next, as the result says."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "note": {
                    "type": "string",
                    "maxLength": NOTE_MAX,
                    "description": "What the person needs and what you tried, briefly.",
                }
            },
            "required": ["note"],
            "additionalProperties": False,
        },
        handler=handler,
        read_only=False,
    )


__all__ = ["HANDOFF_TOOL", "NOTE_MAX", "HandOff", "HandoffRefused", "build_handoff_tool"]
