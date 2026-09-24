"""Tiny real-file builders for parser tests — a hand-built minimal PDF (with
a correct xref table, since pypdf has no tolerance for a missing one) and a
python-docx-generated DOCX. No fixture files checked into the repo; these
build valid bytes in memory."""

from __future__ import annotations

import io


def make_pdf(pages_text: list[str]) -> bytes:
    n_pages = len(pages_text)
    page_ids = list(range(3, 3 + n_pages))
    content_ids = list(range(3 + n_pages, 3 + 2 * n_pages))
    font_id = 3 + 2 * n_pages

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode(),
    ]
    for i in range(n_pages):
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/MediaBox [0 0 200 200] /Contents {content_ids[i]} 0 R >>".encode()
        )
    for text in pages_text:
        stream = f"BT /F1 24 Tf 20 100 Td ({text}) Tj ET".encode()
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    buf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(buf))
        buf += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"

    xref_start = len(buf)
    buf += f"xref\n0 {len(objects) + 1}\n".encode()
    buf += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        buf += f"{off:010d} 00000 n \n".encode()
    trailer = f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF"
    buf += trailer.encode()
    return bytes(buf)


def make_docx(blocks: list[tuple[int | None, str]]) -> bytes:
    """`blocks` is a list of (heading_level_or_None, text)."""
    from docx import Document

    doc = Document()
    for level, text in blocks:
        if level is None:
            doc.add_paragraph(text)
        else:
            doc.add_heading(text, level=level)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
