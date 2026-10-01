"""Stopping a running turn from outside its request (task 1.6).

A turn lives inside one SSE request on one worker, so "stop" has to reach
*that* worker. Same shape as `approval_registry`, for the same reason: an
in-process map of running turns, plus a Redis pub/sub fan-out so an
interrupt that lands on another worker is re-fired where the turn actually
is. Redis is disposable here too — without it, interrupts still work for the
single-worker case.

Since QOS-01 (`services/turns.py`) this is the only way to stop a turn:
dropping the connection no longer does, because a turn runs on the server
and the connection only watches it. The stream ends with a proper `done`,
and the partial answer stays on screen.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import Callable

from app.db.redis import get_redis, subscribe_forever
from app.logging import get_logger

log = get_logger(__name__)

CHANNEL = "turn-interrupts"

#: conversation_id -> callbacks of the turns currently running in it. A set,
#: because two tabs can each have a turn in flight in one conversation.
_running: dict[str, set[Callable[[], None]]] = {}
_listener: asyncio.Task[None] | None = None


def register(conversation_id: uuid.UUID | str, stop: Callable[[], None]) -> None:
    _running.setdefault(str(conversation_id), set()).add(stop)


def unregister(conversation_id: uuid.UUID | str, stop: Callable[[], None]) -> None:
    key = str(conversation_id)
    callbacks = _running.get(key)
    if callbacks is None:
        return
    callbacks.discard(stop)
    if not callbacks:
        _running.pop(key, None)


def interrupt_local(conversation_id: uuid.UUID | str) -> int:
    """Stop every turn running in this conversation on *this* worker.
    Returns how many were stopped; 0 is normal, not an error."""
    callbacks = list(_running.get(str(conversation_id), ()))
    for stop in callbacks:
        stop()
    return len(callbacks)


async def publish(conversation_id: uuid.UUID | str) -> None:
    """Fan an interrupt out to other workers. Best-effort by design."""
    try:
        await get_redis().publish(CHANNEL, json.dumps({"conversation_id": str(conversation_id)}))
    except Exception as exc:
        log.warning("interrupt_publish_failed", error=str(exc)[:200])
        get_redis.cache_clear()


async def _listen() -> None:
    """Re-fire interrupts published by other workers. A dead listener would
    silently strand turns on other workers, so it reconnects if Redis drops."""
    await subscribe_forever(
        CHANNEL,
        lambda payload: interrupt_local(str(payload.get("conversation_id", ""))),
        name="interrupt",
    )


async def start_listener() -> None:
    global _listener  # noqa: PLW0603 - one process-wide subscriber by design
    if _listener is None or _listener.done():
        _listener = asyncio.create_task(_listen())


async def stop_listener() -> None:
    global _listener  # noqa: PLW0603 - see start_listener
    if _listener is not None:
        _listener.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _listener
        _listener = None


__all__ = [
    "CHANNEL",
    "interrupt_local",
    "publish",
    "register",
    "start_listener",
    "stop_listener",
    "unregister",
]
