"""Images sent with a message (Phase 9.6): what both drivers receive.

The real driver puts each one beside the text as a Messages API image
block (base64), sent to the CLI as one streamed user message. The fake
driver only says how many it got, so tests can see they arrived.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PromptImage:
    """An image as the model receives it, next to the message's text."""

    media_type: str
    data: bytes

    def block(self) -> dict[str, Any]:
        """The Messages API's image content block."""
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": self.media_type,
                "data": base64.b64encode(self.data).decode("ascii"),
            },
        }


def content_blocks(prompt: str, images: Sequence[PromptImage]) -> list[dict[str, Any]]:
    """The user message's content: the images first, as the API advises,
    then the text. An empty text block is refused by the API, so a message
    that is only a screenshot has none."""
    blocks = [image.block() for image in images]
    if prompt.strip():
        blocks.append({"type": "text", "text": prompt})
    return blocks


async def streamed_prompt(
    prompt: str, images: Sequence[PromptImage]
) -> AsyncIterator[dict[str, Any]]:
    """The one user message `ClaudeSDKClient.query` streams to the CLI when
    the prompt carries images: a plain string can't."""
    yield {
        "type": "user",
        "message": {"role": "user", "content": content_blocks(prompt, images)},
        "parent_tool_use_id": None,
    }


def count_note(images: Sequence[PromptImage]) -> str:
    """ "[1 image]", "[3 images]": how the fake driver says what it got."""
    n = len(images)
    return f"[{n} image{'s' if n != 1 else ''}]"


__all__ = ["PromptImage", "content_blocks", "count_note", "streamed_prompt"]
