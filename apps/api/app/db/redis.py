"""Async Redis client, cached like the SQLAlchemy engine in ``db/session.py``.

Nothing here talks to Redis at import time — the connection pool is created
lazily on first use so tests never need Redis to be running.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from redis.asyncio import Redis

from app.config import settings
from app.logging import get_logger

log = get_logger(__name__)


@lru_cache
def get_redis() -> Redis:
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=1.0,
        socket_timeout=1.0,
    )


def _subscriber() -> Redis:
    """A client for one long-lived subscription, separate from the shared
    command client so that reconnecting one never throws the other away.

    The listeners used to subscribe on the shared client and block in
    `listen()`. Its 1s socket timeout fired after every quiet second, so they
    dropped the subscription, slept 2s and subscribed again, over and over;
    pub/sub keeps nothing for an absent subscriber, so a cross-worker stop or
    approval decision published in that window (about two thirds of the
    time) was lost. `subscribe_forever` polls `get_message` with its own
    timeout instead, and the health check pings a quiet connection, so a dead
    one is still noticed.
    """
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=1.0,
        socket_timeout=None,
        socket_keepalive=True,
        health_check_interval=15,
    )


#: How long one `get_message` waits before looping (and health-checking).
POLL_S = 5.0
RETRY_S = 2.0


async def subscribe_forever(
    channel: str, handle: Callable[[dict[str, Any]], object], *, name: str
) -> None:
    """Call `handle` with each JSON object published on `channel`, until
    cancelled. Reconnects if Redis drops; a malformed message or a failing
    handler costs that one message, not the subscription."""
    while True:
        client = _subscriber()
        try:
            async with client.pubsub() as pubsub:
                await pubsub.subscribe(channel)
                while True:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=POLL_S
                    )
                    if message is None:
                        continue
                    try:
                        payload = json.loads(message["data"])
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(payload, dict):
                        continue
                    try:
                        handle(payload)
                    except Exception:
                        log.exception(f"{name}_listener_handler_failed")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning(f"{name}_listener_reconnecting", error=str(exc)[:200])
            await asyncio.sleep(RETRY_S)
        finally:
            with contextlib.suppress(Exception):
                await client.aclose()


__all__ = ["POLL_S", "RETRY_S", "get_redis", "subscribe_forever"]
