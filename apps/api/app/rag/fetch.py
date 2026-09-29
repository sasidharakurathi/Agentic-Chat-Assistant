"""Fetching a live URL's main content — distinct from ``parsers/html.py``,
which parses HTML *already in hand* (an uploaded file). A live page has
navigation, ads, and footers around the actual content; ``trafilatura``
specializes in stripping that boilerplate out, which a generic block-tag
walk (like ``parsers/html.py`` does) isn't designed to do.

The URL is a builder's choice and the worker fetches it from inside our
network, so the request goes through the SSRF guard (``app.security.ssrf``):
public addresses only, every redirect re-checked, and a size cap. Before
this, a URL source pointed at ``http://localhost:8000`` or the cloud metadata
address would have been fetched and its contents indexed.
"""

from __future__ import annotations

import asyncio

import trafilatura

from app.security.ssrf import FetchLimits, SsrfBlocked, safe_request

HTTP_ERROR_FROM = 400
#: A web page, not a download: 5 MB of HTML is already far past normal.
PAGE_LIMITS = FetchLimits(timeout_s=30.0, max_bytes=5_000_000, max_redirects=5)


class UrlFetchError(Exception):
    """The page could not be fetched; the message says why, for the source's
    error field."""


async def fetch_url_text(url: str) -> str:
    try:
        result = await safe_request("GET", url, limits=PAGE_LIMITS)
    except SsrfBlocked as exc:
        raise UrlFetchError(f"can't fetch {url}: {exc}") from exc
    if result.status >= HTTP_ERROR_FROM:
        raise UrlFetchError(f"{url} returned HTTP {result.status}")
    charset = "utf-8"
    for part in result.headers.get("content-type", "").split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.lower() == "charset" and value:
            charset = value.strip("\"'")
    try:
        html = result.body.decode(charset, errors="replace")
    except LookupError:
        html = result.body.decode("utf-8", errors="replace")
    extracted = await asyncio.to_thread(
        trafilatura.extract, html, include_comments=False, include_tables=True
    )
    return extracted or ""


__all__ = ["PAGE_LIMITS", "UrlFetchError", "fetch_url_text"]
