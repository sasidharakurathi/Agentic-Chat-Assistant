"""PDF parsing via ``pypdf``. No heading detection — PDF headings are a font
size/weight convention, not stored structure, and reliably inferring them is
its own project. Page markers, on the other hand, are exact: every page's
text is extracted separately, so we know precisely where each one starts in
the flattened ``text``.
"""

from __future__ import annotations

import io

from pypdf import PdfReader

from app.rag.parsers.base import PageMarker, ParsedDocument


def parse_pdf(content: bytes) -> ParsedDocument:
    reader = PdfReader(io.BytesIO(content))
    parts: list[str] = []
    pages: list[PageMarker] = []
    offset = 0
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        pages.append(PageMarker(page=i, offset=offset))
        parts.append(text)
        offset += len(text) + 2

    title = None
    meta = reader.metadata
    if meta and meta.title:
        title = meta.title

    return ParsedDocument(text="\n\n".join(parts), title=title, pages=pages)


__all__ = ["parse_pdf"]
