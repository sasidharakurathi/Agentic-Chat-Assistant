"""HTML file parsing — flattens block-level elements into plain text,
recording heading offsets as it goes. This is for HTML *uploaded as a file*;
fetching a live URL's main content (stripping nav/ads/boilerplate) is a
different problem, handled at ingest time (task 2.5), not here.
"""

from __future__ import annotations

from bs4 import BeautifulSoup
from bs4.element import Tag

from app.rag.parsers.base import Heading, ParsedDocument

_BLOCK_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "blockquote", "td", "th"]


def parse_html(content: bytes) -> ParsedDocument:
    soup = BeautifulSoup(content, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else None

    parts: list[str] = []
    headings: list[Heading] = []
    offset = 0
    for el in soup.find_all(_BLOCK_TAGS):
        if not isinstance(el, Tag):
            continue
        if el.find(_BLOCK_TAGS) is not None:
            # Not a leaf block (e.g. <li><p>...</p></li>) — its block-level
            # descendant(s) get walked instead, so extracting here too would
            # duplicate the text.
            continue
        text = el.get_text(" ", strip=True)
        if not text:
            continue
        if el.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            headings.append(Heading(level=int(el.name[1]), text=text, offset=offset))
        parts.append(text)
        offset += len(text) + 2  # +2 for the "\n\n" joiner added below

    full_text = "\n\n".join(parts)
    return ParsedDocument(text=full_text, title=title, headings=headings)


__all__ = ["parse_html"]
