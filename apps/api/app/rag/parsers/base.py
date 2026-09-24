"""The shared output shape every parser produces, regardless of input format.

``text`` is always the full normalized document as one string — chunking
(``app/rag/chunking.py``) slices it by character offset, so every parser
must keep ``headings``/``pages`` offsets consistent with the exact ``text``
it returns.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Heading:
    level: int  # 1-6, like HTML/Markdown heading levels
    text: str
    offset: int  # character offset into ParsedDocument.text where it starts


@dataclass
class PageMarker:
    page: int  # 1-indexed
    offset: int  # character offset into ParsedDocument.text where the page starts


@dataclass
class ParsedDocument:
    text: str
    title: str | None = None
    headings: list[Heading] = field(default_factory=list)
    # Only PDFs produce real page markers — DOCX/HTML/Markdown/plain text have
    # no stored pagination (it's a rendering-time concept for them), so this
    # stays empty and citations for those formats use char ranges instead.
    pages: list[PageMarker] = field(default_factory=list)

    @property
    def page_count(self) -> int | None:
        return len(self.pages) if self.pages else None


class UnsupportedFormat(ValueError):
    pass


__all__ = ["Heading", "PageMarker", "ParsedDocument", "UnsupportedFormat"]
