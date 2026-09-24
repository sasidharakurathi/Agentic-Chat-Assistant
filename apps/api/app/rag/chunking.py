"""Recursive chunker: splits a ``ParsedDocument``'s text into ``ChunkSpan``s
targeting ``max_tokens`` with ``overlap``, carrying forward heading
breadcrumbs and page numbers for each chunk.

"Recursive" means: try splitting on paragraph breaks first; any piece still
too big gets split again on line breaks, then sentence breaks, then raw
words, then (if a single "word" is still too long — a URL, say) hard
character windows. This is the same strategy popularized by LangChain's
`RecursiveCharacterTextSplitter`, reimplemented here in ~60 lines so this
project doesn't need that dependency for one function.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.rag.parsers.base import Heading, PageMarker, ParsedDocument

# Same len//4 approximation `FakeDriver._estimate_cost` already uses
# (app/agent/driver.py) — good enough for *sizing* chunks; swapping in a
# real tokenizer later doesn't change this module's shape.
_CHARS_PER_TOKEN = 4

_SEPARATORS = ["\n\n", "\n", ". ", " "]


@dataclass
class ChunkSpan:
    ordinal: int
    text: str
    start: int  # char offset into the document's full text
    end: int
    token_count: int
    breadcrumb: list[str] = field(default_factory=list)
    page: int | None = None
    # The page this chunk's text *ends* on. A chunk long enough to straddle a
    # page break genuinely spans a range, and a citation that renders "p. 3"
    # for content that runs 3-5 is quietly lying about where to look.
    page_end: int | None = None


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _split_spans(
    text: str, base_offset: int, max_chars: int, separators: list[str]
) -> list[tuple[int, int]]:
    """(start, end) offsets, absolute (relative to the *original* document,
    not to `text`), splitting `text` into pieces no longer than max_chars."""
    if len(text) <= max_chars:
        return [(base_offset, base_offset + len(text))] if text else []
    if not separators:
        return [
            (base_offset + i, base_offset + min(i + max_chars, len(text)))
            for i in range(0, len(text), max_chars)
        ]

    sep, rest = separators[0], separators[1:]
    raw = text.split(sep)
    # Reattach the separator to every piece but the last, so concatenating
    # pieces back together reproduces `text` exactly (keeps offsets honest).
    pieces = [p + sep for p in raw[:-1]] + raw[-1:]

    spans: list[tuple[int, int]] = []
    buf_start: int | None = None
    buf_end = 0
    pos = base_offset
    for piece in pieces:
        piece_start, piece_end = pos, pos + len(piece)
        if len(piece) > max_chars:
            if buf_start is not None:
                spans.append((buf_start, buf_end))
                buf_start = None
            spans.extend(_split_spans(piece, piece_start, max_chars, rest))
        elif buf_start is not None and (piece_end - buf_start) > max_chars:
            spans.append((buf_start, buf_end))
            buf_start, buf_end = piece_start, piece_end
        else:
            if buf_start is None:
                buf_start = piece_start
            buf_end = piece_end
        pos = piece_end
    if buf_start is not None:
        spans.append((buf_start, buf_end))
    return spans


def _breadcrumb_at(headings: list[Heading], pos: int) -> list[str]:
    """The heading trail active at `pos` — e.g. ["Chapter 2", "2.1 Setup"].
    Assumes `headings` is in document order (every parser builds it that
    way)."""
    stack: list[Heading] = []
    for h in headings:
        if h.offset > pos:
            break
        while stack and stack[-1].level >= h.level:
            stack.pop()
        stack.append(h)
    return [h.text for h in stack]


def _page_at(pages: list[PageMarker], pos: int) -> int | None:
    page: int | None = None
    for p in pages:
        if p.offset > pos:
            break
        page = p.page
    return page


_WHITESPACE = re.compile(r"\s")


def _snap_to_word_start(text: str, pos: int, ceiling: int) -> int:
    """Move `pos` forward to the next word boundary if it landed mid-word.

    `pos` is pure arithmetic (``span_start - overlap_chars``), so it happily
    lands in the middle of a token — a chunk that begins ``"ge 1 content"``
    instead of ``"Page 1 content"``. That costs at most one word of overlap,
    but it breaks anything that has to match the chunk text back against the
    document: a browser text fragment (``#:~:text=``) only matches on word
    boundaries, so a half-word prefix silently highlights nothing.

    `ceiling` is where the chunk's own content begins: the snap only moves
    within the overlap it borrowed. With no boundary there, the chunk starts
    at `ceiling` (no overlap, but whole words). This used to look for a space
    only, and when there was none `find` returned -1, which snapped the start
    to offset 0: in a text of newline-separated tokens (a log, a word list)
    every chunk re-embedded the document from its beginning.
    """
    if pos <= 0 or text[pos - 1].isspace():
        return pos
    boundary = _WHITESPACE.search(text, pos, ceiling)
    return boundary.end() if boundary else ceiling


def chunk_document(doc: ParsedDocument, *, max_tokens: int, overlap: float) -> list[ChunkSpan]:
    max_chars = max(1, max_tokens * _CHARS_PER_TOKEN)
    raw_spans = _split_spans(doc.text, 0, max_chars, list(_SEPARATORS))
    overlap_chars = int(max_chars * overlap)

    chunks: list[ChunkSpan] = []
    for i, (span_start, end) in enumerate(raw_spans):
        start = span_start
        if i > 0:
            # Pull the start back into the previous chunk for context
            # continuity, but never past that chunk's own start.
            start = _snap_to_word_start(
                doc.text, max(raw_spans[i - 1][0], span_start - overlap_chars), span_start
            )
        text = doc.text[start:end]
        chunks.append(
            ChunkSpan(
                ordinal=i,
                text=text,
                start=start,
                end=end,
                token_count=estimate_tokens(text),
                # Locators come from `span_start` — where this chunk's OWN
                # content begins — not from `start`, which has been pulled
                # back into the previous chunk for overlap. Using `start`
                # reports the *previous* page/heading for every chunk after
                # the first, which is exactly the page a "#page=N" deep link
                # would then open.
                breadcrumb=_breadcrumb_at(doc.headings, span_start),
                page=_page_at(doc.pages, span_start),
                page_end=_page_at(doc.pages, max(span_start, end - 1)),
            )
        )
    return chunks


__all__ = ["ChunkSpan", "chunk_document", "estimate_tokens"]
