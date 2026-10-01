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
import codecs

import trafilatura

from app.security.ssrf import FetchLimits, SsrfBlocked, is_compressed, safe_request

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
    if is_compressed(result.headers):
        # We asked for an uncompressed body; one that is compressed anyway is
        # not inflated (its size would be the server's to choose).
        raise UrlFetchError(f"{url} sent a compressed page, which can't be read")
    charset = page_charset(result.headers.get("content-type", ""))
    body = result.body

    def read() -> str:
        html = body.decode(charset, errors="replace")
        return trafilatura.extract(html, include_comments=False, include_tables=True) or ""

    return await asyncio.to_thread(read)


#: Text encodings a page may declare. Python will happily "decode" with
#: codecs that are not text encodings at all: `punycode` takes minutes on a
#: few megabytes (and ran on the event loop), `idna` and `undefined` raise.
_CHARSETS = frozenset(
    {
        "utf-8",
        "utf-16",
        "utf-16-le",
        "utf-16-be",
        "ascii",
        "iso8859-1",
        "iso8859-2",
        "iso8859-15",
        "cp1250",
        "cp1251",
        "cp1252",
        "cp1253",
        "cp1254",
        "cp1256",
        "shift_jis",
        "euc_jp",
        "iso2022_jp",
        "gbk",
        "gb2312",
        "gb18030",
        "big5",
        "euc_kr",
        "koi8-r",
    }
)


def page_charset(content_type: str) -> str:
    """The encoding to read a page with: the one it declares, when that is a
    text encoding we know; otherwise UTF-8."""
    for part in content_type.split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.lower() != "charset" or not value:
            continue
        try:
            name = codecs.lookup(value.strip("\"' ")).name
        except (LookupError, ValueError):
            break
        if name in _CHARSETS:
            return name
        break
    return "utf-8"


__all__ = ["PAGE_LIMITS", "UrlFetchError", "fetch_url_text", "page_charset"]
