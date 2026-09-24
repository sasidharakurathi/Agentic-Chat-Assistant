"""Plain text and Markdown — Markdown's ``text`` stays as markdown source
(never rendered to HTML); only its ``#``-style headings are pulled out for
breadcrumbs. Chunks keep their markdown formatting, which is normal and
desirable for RAG — the LLM reading them handles it fine.
"""

from __future__ import annotations

import re

from app.rag.parsers.base import Heading, ParsedDocument

_ATX_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)


def parse_text(content: bytes) -> ParsedDocument:
    return ParsedDocument(text=content.decode("utf-8", errors="replace"))


def parse_markdown(content: bytes) -> ParsedDocument:
    text = content.decode("utf-8", errors="replace")
    headings = [
        Heading(level=len(m.group(1)), text=m.group(2).strip(), offset=m.start())
        for m in _ATX_HEADING.finditer(text)
    ]
    title = headings[0].text if headings and headings[0].level == 1 else None
    return ParsedDocument(text=text, title=title, headings=headings)


__all__ = ["parse_markdown", "parse_text"]
