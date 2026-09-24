from __future__ import annotations

import pytest
from app.rag.parsers import UnsupportedFormat, parse_by_mime
from app.rag.parsers.docx import parse_docx
from app.rag.parsers.html import parse_html
from app.rag.parsers.pdf import parse_pdf
from app.rag.parsers.text import parse_markdown, parse_text
from tests.rag_fixtures import make_docx, make_pdf


def test_parse_text_is_a_plain_passthrough() -> None:
    doc = parse_text(b"hello\nworld")
    assert doc.text == "hello\nworld"
    assert doc.headings == []
    assert doc.pages == []
    assert doc.page_count is None


def test_parse_markdown_extracts_headings_and_title() -> None:
    md = "# Intro\n\nSome text.\n\n## Details\n\nMore text here.\n"
    doc = parse_markdown(md.encode())
    assert [h.text for h in doc.headings] == ["Intro", "Details"]
    assert [h.level for h in doc.headings] == [1, 2]
    assert doc.title == "Intro"
    # offsets must point at exactly where the heading text starts in doc.text
    h2 = doc.headings[1]
    assert doc.text[h2.offset : h2.offset + 2] == "##"


def test_parse_html_extracts_headings_title_and_skips_script_style() -> None:
    html = b"""
    <html><head><title>Page Title</title>
    <style>body { color: red; }</style></head>
    <body>
      <h1>Welcome</h1>
      <p>First paragraph.</p>
      <script>alert('nope')</script>
      <h2>Section Two</h2>
      <p>Second paragraph.</p>
    </body></html>
    """
    doc = parse_html(html)
    assert doc.title == "Page Title"
    assert [h.text for h in doc.headings] == ["Welcome", "Section Two"]
    assert "alert" not in doc.text
    assert "color: red" not in doc.text
    assert "First paragraph." in doc.text
    assert "Second paragraph." in doc.text


def test_parse_html_does_not_duplicate_nested_block_text() -> None:
    html = b"<ul><li><p>only once</p></li></ul>"
    doc = parse_html(html)
    assert doc.text.count("only once") == 1


def test_parse_pdf_extracts_text_per_page_with_offsets() -> None:
    pdf = make_pdf(["Hello Page One", "Second Page Text"])
    doc = parse_pdf(pdf)
    assert "Hello Page One" in doc.text
    assert "Second Page Text" in doc.text
    assert [p.page for p in doc.pages] == [1, 2]
    assert doc.page_count == 2
    p2 = doc.pages[1]
    assert doc.text[p2.offset :].startswith("Second Page Text")


def test_parse_docx_extracts_heading_styles() -> None:
    docx_bytes = make_docx(
        [
            (1, "Title Heading"),
            (None, "Intro paragraph."),
            (2, "Sub Section"),
            (None, "Body paragraph."),
        ]
    )
    doc = parse_docx(docx_bytes)
    assert [h.text for h in doc.headings] == ["Title Heading", "Sub Section"]
    assert [h.level for h in doc.headings] == [1, 2]
    assert doc.title == "Title Heading"
    assert doc.pages == []  # docx has no real page boundaries


def test_dispatch_by_mime_and_by_extension_fallback() -> None:
    assert parse_by_mime(b"hi", mime="text/plain").text == "hi"
    assert parse_by_mime(b"hi", mime="application/octet-stream", filename="notes.txt").text == "hi"


def test_dispatch_raises_for_unknown_format() -> None:
    with pytest.raises(UnsupportedFormat):
        parse_by_mime(b"???", mime="application/x-nonsense", filename="mystery.bin")
