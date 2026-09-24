"""The waiting room for pending approvals (task 3.8).

`can_use_tool` blocks on a future here while a human decides. The registry is
an **in-process dict**, which is a deliberate constraint worth stating: the
awaiting coroutine lives inside one SSE request on one worker, so the resolve
call has to reach *that* worker. The plan's answer is sticky routing plus a
Redis fallback signal, and both halves are here:

* in-process resolution is instant and is what normally happens;
* a Redis pub/sub listener catches a resolve that landed on a different
  worker and re-fires it locally.

Redis is treated as disposable, the same contract `session_store` established:
if it is down, approvals still work for the common single-worker case and
degrade to a timeout (which denies) rather than breaking the turn.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from typing import Literal, get_args

from app.db.redis import get_redis, subscribe_forever
from app.logging import get_logger

log = get_logger(__name__)

#: "interrupted" is never a reviewer's answer (the resolve endpoint accepts
#: only approved/denied): it is how a Stop settles a wait still in progress.
Decision = Literal["approved", "denied", "expired", "interrupted"]

CHANNEL = "approvals"

#: How long a tool call waits for a human before giving up. Long enough for
#: someone to read a SQL statement and think; short enough that a forgotten
#: tab doesn't hold a database connection and an SSE stream open all day.
DEFAULT_TIMEOUT_S = 300.0

_waiters: dict[str, asyncio.Future[Decision]] = {}
_listener: asyncio.Task[None] | None = None


def register(approval_id: uuid.UUID | str) -> asyncio.Future[Decision]:
    """Claim a slot *before* the approval is announced, so a decision that
    arrives between the insert and the await is not lost."""
    key = str(approval_id)
    future: asyncio.Future[Decision] = asyncio.get_running_loop().create_future()
    _waiters[key] = future
    return future


def resolve_local(approval_id: uuid.UUID | str, decision: Decision) -> bool:
    """Complete a waiter on *this* worker. Returns False when there isn't one
    — which is normal, not an error: the request may have landed elsewhere,
    or the waiter may already have timed out."""
    future = _waiters.pop(str(approval_id), None)
    if future is None or future.done():
        return False
    future.set_result(decision)
    return True


def forget(approval_id: uuid.UUID | str) -> None:
    _waiters.pop(str(approval_id), None)


def pending_count() -> int:
    return len(_waiters)


async def wait(
    approval_id: uuid.UUID | str,
    future: asyncio.Future[Decision],
    # The timeout IS the feature here (an unanswered approval must expire, not
    # hang the turn), so this is not the "pass a cancel scope instead" case
    # ASYNC109 is warning about. Defaulted to None and resolved at call time so
    # the module constant stays overridable rather than frozen at import.
    timeout: float | None = None,  # noqa: ASYNC109
) -> Decision:
    """Block until decided, or `expired` on timeout.

    Timing out yields `expired`, and every caller treats that exactly like
    `denied`. An unattended approval must never become an implicit yes.
    """
    try:
        return await asyncio.wait_for(
            future, timeout=DEFAULT_TIMEOUT_S if timeout is None else timeout
        )
    except TimeoutError:
        log.info("approval_timed_out", approval_id=str(approval_id))
        return "expired"
    finally:
        forget(approval_id)


async def publish(approval_id: uuid.UUID | str, decision: Decision) -> None:
    """Fan a decision out to other workers. Best-effort by design."""
    try:
        await get_redis().publish(
            CHANNEL, json.dumps({"approval_id": str(approval_id), "decision": decision})
        )
    except Exception as exc:
        log.warning("approval_publish_failed", error=str(exc)[:200])
        get_redis.cache_clear()


async def _listen() -> None:
    """Re-fire decisions published by other workers. A dropped Redis
    connection must not silently stop cross-worker approvals for the lifetime
    of the process, so it reconnects."""
    await subscribe_forever(CHANNEL, _on_published, name="approval")


def _on_published(payload: dict[str, object]) -> None:
    decision = payload.get("decision")
    # Anything unrecognised fails closed, as a denial.
    resolve_local(
        str(payload.get("approval_id", "")),
        decision if decision in get_args(Decision) else "denied",  # type: ignore[arg-type]
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
    "DEFAULT_TIMEOUT_S",
    "Decision",
    "forget",
    "pending_count",
    "publish",
    "register",
    "resolve_local",
    "start_listener",
    "stop_listener",
    "wait",
]
