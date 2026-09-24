from __future__ import annotations

from app.rag.parsers.base import ParsedDocument, UnsupportedFormat
from app.rag.parsers.docx import parse_docx
from app.rag.parsers.html import parse_html
from app.rag.parsers.pdf import parse_pdf
from app.rag.parsers.text import parse_markdown, parse_text

_BY_MIME = {
    "text/plain": parse_text,
    "text/markdown": parse_markdown,
    "text/html": parse_html,
    "application/pdf": parse_pdf,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": parse_docx,
}

# Fallbacks for uploads whose browser/client-supplied content type is vague
# or wrong — keyed by file extension, checked when the MIME lookup misses.
_BY_EXTENSION = {
    ".txt": parse_text,
    ".md": parse_markdown,
    ".markdown": parse_markdown,
    ".html": parse_html,
    ".htm": parse_html,
    ".pdf": parse_pdf,
    ".docx": parse_docx,
}


def parse_by_mime(content: bytes, *, mime: str, filename: str = "") -> ParsedDocument:
    parser = _BY_MIME.get(mime.split(";", maxsplit=1)[0].strip().lower())
    if parser is None:
        ext = filename[filename.rfind(".") :].lower() if "." in filename else ""
        parser = _BY_EXTENSION.get(ext)
    if parser is None:
        raise UnsupportedFormat(f"No parser for mime={mime!r} filename={filename!r}")
    return parser(content)


__all__ = ["parse_by_mime"]
