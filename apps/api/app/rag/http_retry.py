"""Retry for the paid HTTP APIs ingestion calls (Voyage, Anthropic).

Both rate-limit, and both are occasionally overloaded. Without a retry a 429
during a large upload lost that request outright: a failed embedding batch
failed the whole source, and a failed context call silently left its chunk
without context. This retries what is worth retrying (rate limits, overload,
5xx, timeouts, dropped connections), honours `Retry-After` when the server
sends one, and otherwise backs off exponentially with jitter, so parallel
requests don't all come back at the same instant.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx

from app.logging import get_logger

log = get_logger(__name__)

#: 408 timeout, 409 conflict (Anthropic uses it for transient lock errors),
#: 429 rate limited, 5xx server errors, 529 Anthropic "overloaded".
RETRY_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504, 529})
MAX_ATTEMPTS = 5
BASE_DELAY_S = 1.0
MAX_DELAY_S = 30.0

# A seam for tests, so a retry does not really sleep.
_sleep = asyncio.sleep


def _backoff(attempt: int) -> float:
    ceiling = min(MAX_DELAY_S, BASE_DELAY_S * 2 ** (attempt - 1))
    return ceiling * (0.5 + random.random() / 2)


def _retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return min(MAX_DELAY_S, max(0.0, float(raw)))
    except ValueError:
        return None  # an HTTP date: rare enough to fall back to backoff


async def post_with_retry(
    client: httpx.AsyncClient, url: str, *, attempts: int = MAX_ATTEMPTS, **kwargs: Any
) -> httpx.Response:
    """`client.post`, retried. Returns the last response (the caller decides
    what a non-2xx means) or raises the last transport error."""
    for attempt in range(1, attempts + 1):
        try:
            resp = await client.post(url, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if attempt == attempts:
                raise
            delay, why = _backoff(attempt), type(exc).__name__
        else:
            if resp.status_code not in RETRY_STATUSES or attempt == attempts:
                return resp
            delay, why = _retry_after(resp) or _backoff(attempt), str(resp.status_code)
        log.info("http_retry", host=httpx.URL(url).host, reason=why, attempt=attempt, delay_s=delay)
        await _sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


__all__ = ["MAX_ATTEMPTS", "RETRY_STATUSES", "post_with_retry"]
