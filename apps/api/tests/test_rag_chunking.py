from __future__ import annotations

from itertools import pairwise

from app.rag.chunking import chunk_document, estimate_tokens
from app.rag.parsers.base import Heading, PageMarker, ParsedDocument


def test_empty_document_produces_no_chunks() -> None:
    assert chunk_document(ParsedDocument(text=""), max_tokens=100, overlap=0.0) == []


def test_short_document_is_a_single_chunk() -> None:
    doc = ParsedDocument(text="a short paragraph that easily fits in one chunk.")
    chunks = chunk_document(doc, max_tokens=100, overlap=0.0)
    assert len(chunks) == 1
    assert chunks[0].text == doc.text
    assert chunks[0].start == 0
    assert chunks[0].end == len(doc.text)
    assert chunks[0].ordinal == 0


def test_splits_long_document_on_paragraph_boundaries() -> None:
    paragraphs = [
        f"Paragraph number {i} with some filler words to add length. " * 3 for i in range(6)
    ]
    doc = ParsedDocument(text="\n\n".join(paragraphs))
    chunks = chunk_document(doc, max_tokens=40, overlap=0.0)
    assert len(chunks) > 1
    # every chunk fits its budget (generously, since estimate_tokens is approximate)
    for c in chunks:
        assert c.token_count <= 40 * 1.2
    # concatenating spans (ignoring overlap) reconstructs contiguous coverage
    assert chunks[0].start == 0
    assert chunks[-1].end == len(doc.text)


def test_overlap_pulls_chunk_start_back_into_previous_chunk() -> None:
    paragraphs = [f"Paragraph {i}. " * 20 for i in range(5)]
    doc = ParsedDocument(text="\n\n".join(paragraphs))
    no_overlap = chunk_document(doc, max_tokens=30, overlap=0.0)
    with_overlap = chunk_document(doc, max_tokens=30, overlap=0.3)
    assert len(with_overlap) >= 2
    # with overlap, every chunk after the first starts earlier than its
    # no-overlap counterpart (or at the same place, if already at a
    # paragraph boundary the overlap window can't cross usefully)
    for a, b in zip(no_overlap[1:], with_overlap[1:], strict=True):
        assert b.start <= a.start


def test_a_single_oversized_paragraph_recurses_into_smaller_pieces() -> None:
    huge = "word " * 500  # no paragraph breaks at all — forces recursion
    doc = ParsedDocument(text=huge)
    chunks = chunk_document(doc, max_tokens=20, overlap=0.0)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c.text) <= 20 * 4 + 1  # _CHARS_PER_TOKEN=4, +1 slack for split boundary


def test_breadcrumb_and_page_are_attached_from_the_parsed_document() -> None:
    text = "Intro text.\n\nBody under heading one.\n\nBody under heading two."
    h1_offset = text.index("Body under heading one")
    h2_offset = text.index("Body under heading two")
    doc = ParsedDocument(
        text=text,
        headings=[
            Heading(level=1, text="Chapter", offset=0),
            Heading(level=2, text="Section One", offset=h1_offset),
            Heading(level=2, text="Section Two", offset=h2_offset),
        ],
        pages=[PageMarker(page=1, offset=0), PageMarker(page=2, offset=h2_offset)],
    )
    chunks = chunk_document(doc, max_tokens=5, overlap=0.0)

    first = chunks[0]
    assert first.breadcrumb == ["Chapter"]
    assert first.page == 1

    section_two_chunk = next(c for c in chunks if "two." in c.text)
    assert section_two_chunk.breadcrumb == ["Chapter", "Section Two"]
    assert section_two_chunk.page == 2


def test_estimate_tokens_matches_the_len_over_four_heuristic() -> None:
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 40) == 10
    assert estimate_tokens("") == 1  # never zero — avoids div-by-zero downstream


# ── locators under real overlap (task 2.9) ───────────────────
#
# The tests above all run at overlap=0.0, where `start` and the chunk's own
# content start are the same offset. Production runs at overlap=0.15, where
# they are not — and every locator derived from the wrong one is off by a
# page or a heading. That is invisible until something actually follows a
# citation's deep link, which is what 2.9 added.


def _paged_document(pages: int = 6, per_page: int = 120) -> ParsedDocument:
    """Mirrors how parse_pdf lays a document out: page bodies joined by a
    blank line, with a PageMarker at each page's first character."""
    text = ""
    marks = []
    for page in range(1, pages + 1):
        marks.append(PageMarker(page=page, offset=len(text)))
        text += f"Page {page} content. " * per_page + "\n\n"
    return ParsedDocument(text=text, pages=marks)


def test_page_is_where_the_chunks_own_content_starts_not_where_overlap_starts() -> None:
    doc = _paged_document()
    chunks = chunk_document(doc, max_tokens=800, overlap=0.15)
    assert len(chunks) > 3
    for c in chunks:
        # The chunk body is dominated by one page's text; the reported page
        # must be that one, not the page the overlap prefix was borrowed from.
        assert c.text.count(f"Page {c.page} content") > 0
        body = c.text[int(len(c.text) * 0.2) :]
        assert f"Page {c.page} content" in body


def test_breadcrumb_follows_the_chunks_own_content_under_overlap() -> None:
    text = "Intro paragraph.\n\n" + ("Alpha body sentence. " * 60) + "\n\n" + ("Beta body. " * 60)
    beta_offset = text.index("Beta body.")
    doc = ParsedDocument(
        text=text,
        headings=[
            Heading(level=1, text="Section One", offset=0),
            Heading(level=1, text="Section Two", offset=beta_offset),
        ],
    )
    chunks = chunk_document(doc, max_tokens=170, overlap=0.15)
    beta_chunk = next(c for c in chunks if c.start <= beta_offset <= c.end and "Beta" in c.text)
    # Its content begins under Section Two; the overlap prefix reaching back
    # into Section One must not relabel it.
    assert beta_chunk.breadcrumb == ["Section Two"]


def test_page_end_records_a_chunk_that_straddles_a_page_break() -> None:
    doc = _paged_document(pages=6, per_page=40)
    chunks = chunk_document(doc, max_tokens=800, overlap=0.0)
    straddling = [c for c in chunks if c.page_end is not None and c.page_end > (c.page or 0)]
    assert straddling, "expected at least one chunk spanning a page break"


def test_no_chunk_begins_in_the_middle_of_a_word() -> None:
    """A browser text fragment (#:~:text=) only matches on word boundaries, so
    a chunk starting 'ge 1 content' silently highlights nothing."""
    doc = _paged_document()
    for c in chunk_document(doc, max_tokens=800, overlap=0.15):
        if c.start == 0:
            continue
        assert doc.text[c.start - 1].isspace(), (
            f"chunk {c.ordinal} starts mid-word: {c.text[:20]!r}"
        )


def test_overlap_still_pulls_chunk_starts_backward_after_word_snapping() -> None:
    doc = _paged_document(pages=3)
    chunks = chunk_document(doc, max_tokens=800, overlap=0.15)
    for prev, cur in pairwise(chunks):
        assert cur.start < prev.end, "overlap must survive the word-boundary snap"


def test_a_text_without_spaces_never_snaps_back_to_the_document_start() -> None:
    """Newline-separated tokens and no spaces (a log, a CSV column, a word
    list). The snap looked only for " ", and when there was none `find`
    returned -1, so the chunk started at offset 0 and re-embedded the whole
    document up to that point."""
    doc = ParsedDocument(text="\n".join(f"entry{i:05d}" for i in range(3000)))
    chunks = chunk_document(doc, max_tokens=200, overlap=0.15)
    assert len(chunks) > 3
    max_chars = 200 * 4
    for prev, cur in pairwise(chunks):
        assert cur.start > prev.start, f"chunk {cur.ordinal} jumped back to {cur.start}"
        assert len(cur.text) <= max_chars * 1.2, f"chunk {cur.ordinal} is {len(cur.text)} chars"
        assert doc.text[cur.start - 1].isspace(), f"chunk {cur.ordinal} starts mid-word"


def test_a_newline_is_a_word_boundary_too() -> None:
    """The nearest boundary after the overlap point is a newline; the snap
    must stop there, not skip ahead to the next space and lose overlap."""
    para = "\n".join(["alpha beta gamma"] + ["x" * 30] * 40)
    doc = ParsedDocument(text=f"{para}\n\n{para}\n\n{para}")
    for prev, cur in pairwise(chunk_document(doc, max_tokens=150, overlap=0.2)):
        assert cur.start < prev.end
        assert doc.text[cur.start - 1].isspace()
