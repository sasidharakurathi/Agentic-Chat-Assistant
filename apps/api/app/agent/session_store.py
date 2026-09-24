"""Cache of the Agent SDK's ``session_id`` (per conversation).

Postgres (``Conversation.sdk_session_id``) is the source of truth and decides
which session a turn resumes (``chat._session_to_resume``); this copy is
written after each turn's commit and repaired whenever it disagrees, so it
can be stale or missing without consequence. (The chat path loads the row
anyway, so today the cache saves no round trip; it is kept in step for
readers that don't.) It is treated as disposable: every operation here
swallows *any* failure (not just ``RedisError`` — a stale connection can also surface as a plain
``ConnectionError``/``RuntimeError``, e.g. after a network blip) and logs a
warning instead of failing the turn. On failure we also drop the cached
client so the next call opens a fresh connection instead of retrying a
permanently broken one.
"""

from __future__ import annotations

import uuid

from app.db.redis import get_redis
from app.logging import get_logger

log = get_logger(__name__)

_KEY_PREFIX = "sdk-session:"
_TTL_SECONDS = 60 * 24 * 3600  # 60 days after the last turn (rewritten every turn)


def _key(conversation_id: uuid.UUID) -> str:
    return f"{_KEY_PREFIX}{conversation_id}"


async def get(conversation_id: uuid.UUID) -> str | None:
    try:
        value = await get_redis().get(_key(conversation_id))
    except Exception as exc:
        log.warning("session_store_get_failed", error=str(exc))
        get_redis.cache_clear()
        return None
    return value.decode() if isinstance(value, bytes) else value


async def set(conversation_id: uuid.UUID, sdk_session_id: str) -> None:
    try:
        await get_redis().set(_key(conversation_id), sdk_session_id, ex=_TTL_SECONDS)
    except Exception as exc:
        log.warning("session_store_set_failed", error=str(exc))
        get_redis.cache_clear()


async def delete(conversation_id: uuid.UUID) -> None:
    try:
        await get_redis().delete(_key(conversation_id))
    except Exception as exc:
        log.warning("session_store_delete_failed", error=str(exc))
        get_redis.cache_clear()


__all__ = ["delete", "get", "set"]
