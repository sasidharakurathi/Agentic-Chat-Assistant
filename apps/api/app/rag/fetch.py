"""Fetching a live URL's main content — distinct from ``parsers/html.py``,
which parses HTML *already in hand* (an uploaded file). A live page has
navigation, ads, and footers around the actual content; ``trafilatura``
specializes in stripping that boilerplate out, which a generic block-tag
walk (like ``parsers/html.py`` does) isn't designed to do.
"""

from __future__ import annotations

import asyncio

import httpx
import trafilatura


async def fetch_url_text(url: str) -> str:
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": "AssistantStudio/1.0"})
        resp.raise_for_status()
        html = resp.text
    extracted = await asyncio.to_thread(
        trafilatura.extract, html, include_comments=False, include_tables=True
    )
    return extracted or ""


__all__ = ["fetch_url_text"]
