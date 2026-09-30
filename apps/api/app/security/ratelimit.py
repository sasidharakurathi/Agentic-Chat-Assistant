"""Rate limiting: token buckets per IP, user and org (task 5.8).

A bucket holds up to `capacity` tokens and refills at `capacity / period`
tokens a second; a request takes one, and waits (429, `Retry-After`) when
none is left. So "30/60" allows a burst of 30, then one every two seconds.

**In Redis, atomically.** One Lua script reads, refills, takes and writes a
bucket, with Redis's own clock, so every API instance shares the same
limits and two requests can't both take the last token.

**When Redis is down** the limits still hold, per API process, from an
in-memory copy of the same algorithm, rather than every limit silently
lifting. The API keeps working either way: Redis is not in the request path
for anything else a user needs.

Keys are hashed (`rl:<bucket>:<sha256>`): an email address or an IP isn't
written to Redis in the clear.
"""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass

from redis.exceptions import RedisError

from app.config import settings
from app.db.redis import get_redis
from app.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class Limit:
    capacity: int
    period_s: float

    @property
    def rate(self) -> float:
        return self.capacity / self.period_s

    @classmethod
    def parse(cls, text: str) -> Limit:
        """ "30/60" -> 30 requests, refilling over 60 seconds."""
        count, _, seconds = text.partition("/")
        limit = cls(int(count), float(seconds or 1))
        if limit.capacity < 1 or limit.period_s <= 0:
            raise ValueError(f"bad rate limit {text!r}")
        return limit


@dataclass(frozen=True)
class Decision:
    allowed: bool
    remaining: int
    #: Seconds until a token is back (0 when allowed).
    retry_after_s: float


# KEYS[1] bucket; ARGV capacity, rate (tokens/s), cost. Redis's clock, so API
# instances agree; the key expires once it would be full again anyway.
_SCRIPT = """
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local b = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(b[1])
local ts = tonumber(b[2])
if tokens == nil or ts == nil then
  tokens = capacity
  ts = now
end
tokens = math.min(capacity, tokens + math.max(0, now - ts) * rate)
local allowed = 0
local retry = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
else
  retry = (cost - tokens) / rate
end
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', tostring(now))
redis.call('PEXPIRE', KEYS[1], math.ceil(capacity / rate * 1000) + 1000)
return {allowed, tostring(tokens), tostring(retry)}
"""


class _Memory:
    """The same bucket in this process: the fallback, and the tests'."""

    def __init__(self) -> None:
        self.buckets: dict[str, tuple[float, float]] = {}

    def take(self, key: str, limit: Limit, cost: int, now: float | None = None) -> Decision:
        now = time.monotonic() if now is None else now
        tokens, ts = self.buckets.get(key, (float(limit.capacity), now))
        tokens = min(limit.capacity, tokens + max(0.0, now - ts) * limit.rate)
        if tokens >= cost:
            self.buckets[key] = (tokens - cost, now)
            return Decision(True, math.floor(tokens - cost), 0.0)
        self.buckets[key] = (tokens, now)
        return Decision(False, 0, (cost - tokens) / limit.rate)


memory = _Memory()
#: "Redis is down" is logged at most this often, not per request.
_WARN_EVERY_S = 60.0
_warned = {"at": -_WARN_EVERY_S}


def _key(bucket: str, subject: str) -> str:
    return f"rl:{bucket}:{hashlib.sha256(subject.encode()).hexdigest()[:32]}"


def _use_redis() -> bool:
    # The unit tests run without Redis, and each test's requests would share
    # buckets anyway: they use the in-process copy.
    return settings.app_env != "test"


async def take(bucket: str, subject: str, limit: Limit, cost: int = 1) -> Decision:
    """Take `cost` tokens from `bucket` for `subject` (an IP, a user id...)."""
    key = _key(bucket, subject)
    if _use_redis():
        try:
            allowed, tokens, retry = await get_redis().eval(  # type: ignore[misc]
                _SCRIPT, 1, key, str(limit.capacity), repr(limit.rate), str(cost)
            )
            return Decision(bool(int(allowed)), math.floor(float(tokens)), float(retry))
        except (RedisError, OSError) as exc:
            if time.monotonic() - _warned["at"] > _WARN_EVERY_S:
                _warned["at"] = time.monotonic()
                log.warning("rate_limit_redis_unavailable", error=type(exc).__name__)
    return memory.take(key, limit, cost)


__all__ = ["Decision", "Limit", "memory", "take"]
