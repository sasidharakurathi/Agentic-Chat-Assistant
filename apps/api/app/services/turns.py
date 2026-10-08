"""Detached turns (QOS-01): a turn runs on the server, not on a connection.

A turn used to live inside the request that streamed it. Reloading the
page, switching to another conversation or losing the network closed the
request, and with it the turn: recorded as aborted, its approval closed,
its answer lost. Only one conversation at a time could be answering for a
person, and only while they watched.

Now the request starts the turn and lets go of it:

- `start` runs the turn as a task of its own, with its own database
  session. Each event it produces is appended to the turn's **log**.
- Whoever wants to watch **attaches** to the log: from the start (a page
  that just loaded, or the request that sent the message), or after the
  last event it saw. Any number can watch; none of them is needed.
- A conversation has at most one turn running. Its id is kept under the
  conversation (`live`) while it runs, so a page can ask "is something
  being written here?" and attach to it.
- Stop is unchanged (`interrupts`), and an approval still waits for its
  decision, or its timeout, whether or not anyone is watching.

The log is a Redis stream, so a page can attach through any API process.
When Redis cannot be reached, a turn falls back to a log in this process's
memory: it still runs and can still be watched, only not through another
process. Tests use the memory log.

A turn belongs to the process running it. If that process stops, the turn
stops with it: a watcher notices the turn's heartbeat is gone and is told
so, instead of waiting for ever.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol

from redis.asyncio import Redis

from app.agent.events import ErrorEvent, sse_frame
from app.config import settings
from app.db.redis import get_redis
from app.db.session import get_sessionmaker
from app.logging import get_logger
from app.services import chat

log = get_logger(__name__)

#: How long a finished turn's events can still be read: a page that comes
#: back a few minutes later still gets the whole answer.
KEEP_S = 600
#: A running turn says it is alive this often, and is taken for gone when
#: it has not for `LIVE_TTL_S`.
HEARTBEAT_S = 10.0
LIVE_TTL_S = 30
#: Longest a watcher waits for news before checking the turn is alive.
BLOCK_S = 5.0
#: Most events one turn's log keeps (a long answer is a few thousand).
MAX_EVENTS = 50_000

#: What a watcher is told when the turn's process stopped mid-answer.
LOST = sse_frame(
    ErrorEvent(
        code="turn_lost",
        message="The server stopped while this answer was being written. Send the message again.",
        retryable=True,
    )
)


class TurnInProgress(Exception):
    """A turn is already running in this conversation."""


class TurnLog(Protocol):
    async def claim(self, conversation_id: str, turn_id: str) -> bool: ...
    async def live(self, conversation_id: str) -> str | None: ...
    async def heartbeat(self, conversation_id: str, turn_id: str) -> None: ...
    async def release(self, conversation_id: str, turn_id: str) -> None: ...
    async def append(self, conversation_id: str, turn_id: str, frame: str) -> None: ...
    async def finish(self, conversation_id: str, turn_id: str) -> None: ...
    def follow(
        self, conversation_id: str, turn_id: str, after: str | None
    ) -> AsyncIterator[tuple[str, str]]: ...
    async def known(self, conversation_id: str, turn_id: str) -> bool: ...


# ── in this process's memory ────────────────────────────────


@dataclass
class _Turn:
    conversation_id: str
    events: list[str] = field(default_factory=list)
    ended_at: float | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)


class MemoryLog:
    """The log in this process. Event ids are positions: "1", "2", ..."""

    def __init__(self) -> None:
        self._turns: dict[str, _Turn] = {}
        self._live: dict[str, str] = {}

    def _prune(self) -> None:
        now = time.monotonic()
        for turn_id, turn in list(self._turns.items()):
            if turn.ended_at is not None and now - turn.ended_at > KEEP_S:
                del self._turns[turn_id]

    async def claim(self, conversation_id: str, turn_id: str) -> bool:
        self._prune()
        if conversation_id in self._live:
            return False
        self._live[conversation_id] = turn_id
        self._turns[turn_id] = _Turn(conversation_id)
        return True

    async def live(self, conversation_id: str) -> str | None:
        return self._live.get(conversation_id)

    async def heartbeat(self, conversation_id: str, turn_id: str) -> None:
        return None

    async def release(self, conversation_id: str, turn_id: str) -> None:
        if self._live.get(conversation_id) == turn_id:
            del self._live[conversation_id]

    def _turn(self, conversation_id: str, turn_id: str) -> _Turn | None:
        turn = self._turns.get(turn_id)
        return turn if turn is not None and turn.conversation_id == conversation_id else None

    async def append(self, conversation_id: str, turn_id: str, frame: str) -> None:
        turn = self._turn(conversation_id, turn_id)
        if turn is None:
            return
        turn.events.append(frame)
        turn.changed.set()

    async def finish(self, conversation_id: str, turn_id: str) -> None:
        turn = self._turn(conversation_id, turn_id)
        if turn is None:
            return
        turn.ended_at = time.monotonic()
        turn.changed.set()

    async def known(self, conversation_id: str, turn_id: str) -> bool:
        return self._turn(conversation_id, turn_id) is not None

    async def follow(
        self, conversation_id: str, turn_id: str, after: str | None
    ) -> AsyncIterator[tuple[str, str]]:
        turn = self._turn(conversation_id, turn_id)
        if turn is None:
            return
        position = int(after) if after and after.isdigit() else 0
        while True:
            while position < len(turn.events):
                position += 1
                yield str(position), turn.events[position - 1]
            if turn.ended_at is not None:
                return
            turn.changed.clear()
            # Checked again after clearing: an event appended in between
            # would otherwise wait for the next one.
            if position < len(turn.events) or turn.ended_at is not None:
                continue
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(turn.changed.wait(), BLOCK_S)


# ── in Redis ────────────────────────────────────────────────


def _live_key(conversation_id: str) -> str:
    return f"turn:live:{conversation_id}"


def _events_key(conversation_id: str, turn_id: str) -> str:
    # Under the conversation: a turn can only be read through the
    # conversation it belongs to, which the route has already checked the
    # caller may see. A turn id alone opens nothing.
    return f"turn:events:{conversation_id}:{turn_id}"


def _reader() -> Redis:
    """A client that may block longer than the shared one's 1 s timeout."""
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=1.0,
        socket_timeout=BLOCK_S + 5,
    )


class RedisLog:
    """The log as a Redis stream per turn (`turn:events:<id>`), and the
    running turn per conversation as a key that expires unless the turn
    keeps saying it is alive (`turn:live:<conversation>`)."""

    async def claim(self, conversation_id: str, turn_id: str) -> bool:
        ok = await get_redis().set(_live_key(conversation_id), turn_id, nx=True, ex=LIVE_TTL_S)
        return bool(ok)

    async def live(self, conversation_id: str) -> str | None:
        value = await get_redis().get(_live_key(conversation_id))
        return str(value) if value else None

    async def heartbeat(self, conversation_id: str, turn_id: str) -> None:
        redis = get_redis()
        if await redis.get(_live_key(conversation_id)) == turn_id:
            await redis.expire(_live_key(conversation_id), LIVE_TTL_S)
        await redis.expire(_events_key(conversation_id, turn_id), KEEP_S + LIVE_TTL_S)

    async def release(self, conversation_id: str, turn_id: str) -> None:
        redis = get_redis()
        if await redis.get(_live_key(conversation_id)) == turn_id:
            await redis.delete(_live_key(conversation_id))

    async def append(self, conversation_id: str, turn_id: str, frame: str) -> None:
        key = _events_key(conversation_id, turn_id)
        async with get_redis().pipeline(transaction=False) as pipe:
            pipe.xadd(key, {"f": frame}, maxlen=MAX_EVENTS, approximate=True)
            pipe.expire(key, KEEP_S + LIVE_TTL_S)
            await pipe.execute()

    async def finish(self, conversation_id: str, turn_id: str) -> None:
        key = _events_key(conversation_id, turn_id)
        async with get_redis().pipeline(transaction=False) as pipe:
            pipe.xadd(key, {"end": "1"}, maxlen=MAX_EVENTS, approximate=True)
            pipe.expire(key, KEEP_S)
            await pipe.execute()

    async def known(self, conversation_id: str, turn_id: str) -> bool:
        if await self.live(conversation_id) == turn_id:
            return True
        return bool(await get_redis().exists(_events_key(conversation_id, turn_id)))

    async def _ended(self, conversation_id: str, turn_id: str) -> bool:
        last = await get_redis().xrevrange(_events_key(conversation_id, turn_id), count=1)
        return bool(last) and "end" in last[0][1]

    async def follow(
        self, conversation_id: str, turn_id: str, after: str | None
    ) -> AsyncIterator[tuple[str, str]]:
        key = _events_key(conversation_id, turn_id)
        last = after or "0-0"
        reader = _reader()
        try:
            while True:
                found = await reader.xread({key: last}, block=int(BLOCK_S * 1000), count=500)
                if not found:
                    # Nothing new for a while: is anyone still writing?
                    if await self.live(conversation_id) != turn_id and not await self._ended(
                        conversation_id, turn_id
                    ):
                        yield "lost", LOST
                        return
                    continue
                for _key, entries in found:
                    for event_id, fields in entries:
                        last = event_id
                        if "end" in fields:
                            return
                        yield event_id, fields["f"]
        finally:
            with contextlib.suppress(Exception):
                await reader.aclose()


# ── choosing ────────────────────────────────────────────────

_memory = MemoryLog()
_redis = RedisLog()
#: Turns whose log fell back to memory, by id: watchers in this process
#: must read them there.
_in_memory: set[str] = set()


def _primary() -> TurnLog:
    # Tests run with no Redis to rely on, like the rate limiter.
    return _memory if settings.app_env == "test" else _redis


def _log_for(turn_id: str) -> TurnLog:
    return _memory if turn_id in _in_memory else _primary()


async def running_turn(conversation_id: uuid.UUID | str) -> str | None:
    """The id of the turn running in this conversation, or None."""
    cid = str(conversation_id)
    found = await _memory.live(cid)
    if found:
        return found
    if _primary() is _memory:
        return None
    try:
        return await _redis.live(cid)
    except Exception as exc:
        log.warning("turn_lookup_failed", error=type(exc).__name__)
        return None


async def running_among(conversation_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """Which of these conversations have a turn running: for a list page."""
    running = {cid for cid in conversation_ids if str(cid) in _memory._live}
    rest = [cid for cid in conversation_ids if cid not in running]
    if not rest or _primary() is _memory:
        return running
    try:
        values = await get_redis().mget([_live_key(str(cid)) for cid in rest])
    except Exception as exc:
        log.warning("turn_lookup_failed", error=type(exc).__name__)
        return running
    return running | {cid for cid, v in zip(rest, values, strict=True) if v}


async def live_anywhere(conversation_ids: list[uuid.UUID]) -> set[uuid.UUID] | None:
    """Which of these have a turn running in *any* process, or None when
    that can't be known (Phase 7a.7). Unlike `running_among`, which answers
    for a page and may guess, this is for deciding that a turn is dead: with
    no shared log (tests, or no Redis) another process's turns are invisible,
    and a lookup that fails proves nothing."""
    if _primary() is _memory:
        return None
    if not conversation_ids:
        return set()
    try:
        values = await get_redis().mget([_live_key(str(cid)) for cid in conversation_ids])
    except Exception as exc:
        log.warning("turn_lookup_failed", error=type(exc).__name__)
        return None
    return {cid for cid, v in zip(conversation_ids, values, strict=True) if v}


# ── running one ─────────────────────────────────────────────

#: The tasks running turns in this process. Held so they are not collected
#: mid-turn, and so a shutdown can stop them properly.
_tasks: set[asyncio.Task[None]] = set()


async def start(conversation_id: uuid.UUID, text: str) -> str:
    """Start a turn and return its id. It runs whether or not anyone
    watches. Raises `TurnInProgress` if one is already running here."""
    cid = str(conversation_id)
    turn_id = uuid.uuid4().hex
    turn_log = _primary()
    try:
        claimed = await turn_log.claim(cid, turn_id)
    except Exception as exc:
        log.warning("turn_log_unavailable", error=type(exc).__name__)
        turn_log = _memory
        _in_memory.add(turn_id)
        claimed = await _memory.claim(cid, turn_id)
    if not claimed:
        raise TurnInProgress(cid)
    if turn_log is _memory and _primary() is not _memory:
        _in_memory.add(turn_id)

    task = asyncio.create_task(_drive(turn_log, cid, turn_id, text), name=f"turn:{turn_id}")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return turn_id


async def _drive(turn_log: TurnLog, cid: str, turn_id: str, text: str) -> None:
    """Run one turn to its end, appending every event to its log."""
    failed_append = False

    async def emit(frame: str) -> None:
        nonlocal failed_append
        try:
            await turn_log.append(cid, turn_id, frame)
        except Exception as exc:
            # The turn carries on and is saved; only watchers miss it.
            if not failed_append:
                log.warning("turn_log_append_failed", error=type(exc).__name__)
            failed_append = True

    async def beat() -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            with contextlib.suppress(Exception):
                await turn_log.heartbeat(cid, turn_id)

    beating = asyncio.create_task(beat())
    try:
        async with (
            get_sessionmaker()() as session,
            contextlib.aclosing(
                chat.run_message(session, conversation_id=uuid.UUID(cid), text=text)
            ) as events,
        ):
            async for event in events:
                await emit(sse_frame(event))
    except asyncio.CancelledError:
        # The process is stopping: the turn was recorded as aborted on
        # the way out (chat._run_admitted). Watchers are told.
        await emit(LOST)
        raise
    except Exception:
        log.exception("turn_failed", conversation_id=cid, turn_id=turn_id)
        # The exception's own text stays in the log: it can name
        # tables, paths or a driver's connection details.
        await emit(
            sse_frame(
                ErrorEvent(
                    code="internal_error",
                    message="Something went wrong on the server while answering. "
                    "Try again; if it keeps happening, check the server log.",
                )
            )
        )
    finally:
        beating.cancel()
        # The end marker first, then the claim: a watcher that finds the
        # claim gone and no end would take the turn for lost.
        with contextlib.suppress(Exception):
            await turn_log.finish(cid, turn_id)
        with contextlib.suppress(Exception):
            await turn_log.release(cid, turn_id)


async def follow(
    conversation_id: uuid.UUID | str, turn_id: str, after: str | None = None
) -> AsyncIterator[tuple[str, str]]:
    """Every event of the turn after `after` (all of them without it), then
    whatever it produces next, until it ends. Yields (event id, frame)."""
    turn_log = _log_for(turn_id)
    async for item in turn_log.follow(str(conversation_id), turn_id, after):
        yield item


async def known(conversation_id: uuid.UUID | str, turn_id: str) -> bool:
    """Whether this turn, of this conversation, can still be read."""
    try:
        return await _log_for(turn_id).known(str(conversation_id), turn_id)
    except Exception:
        return False


async def shutdown(wait_s: float = 10.0) -> None:
    """Stop this process's turns, each recorded as aborted, before it exits.
    Without this a restart dropped them with no record at all."""
    if not _tasks:
        return
    for task in list(_tasks):
        task.cancel()
    await asyncio.wait(list(_tasks), timeout=wait_s)


def running_here() -> int:
    """How many turns this process is running."""
    return len(_tasks)


__all__ = [
    "KEEP_S",
    "LOST",
    "MemoryLog",
    "RedisLog",
    "TurnInProgress",
    "follow",
    "known",
    "live_anywhere",
    "running_among",
    "running_here",
    "running_turn",
    "shutdown",
    "start",
]
