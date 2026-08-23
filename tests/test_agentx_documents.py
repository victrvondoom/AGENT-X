"""
Reading an upload, and being honest about what could not be read.

The property under test is not "PDFs work". It is that every outcome is
DISTINGUISHABLE: a decoded text file, an extracted PDF, a scan with no text
layer, an encrypted file, and a missing library are five different situations,
and evidence that Agent X could not read must never reach the case as an empty
string that looks like an empty document.
"""
from __future__ import annotations

import pytest

from agentx import documents


def _pdf(lines: list[str]) -> bytes:
    """A minimal, valid single-page PDF containing `lines` as real page text.

    Built by hand rather than with a PDF library so the test does not depend on a
    writer to prove the reader works.
    """
    body = "BT /F1 12 Tf 72 720 Td 14 TL\n"
    for line in lines:
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        body += f"({escaped}) Tj T*\n"
    body += "ET"
    stream = body.encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream
        + b"\nendstream",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_at}\n%%EOF\n").encode()
    return bytes(out)


# ─────────────────────────────────────────────────────────── text
def test_plain_text_is_decoded():
    read = documents.extract(b"Refund of 42.50 was never applied.", "note.txt",
                             "text/plain")
    assert read.method == "utf8"
    assert read.has_text
    assert "42.50" in read.text


def test_undecodable_bytes_do_not_raise():
    read = documents.extract(b"\xff\xfe invalid \x00 bytes", "x.txt", "text/plain")
    assert read.method == "utf8"


# ─────────────────────────────────────────────────────────── pdf
def test_pdf_text_layer_is_extracted():
    pytest.importorskip("pypdf")
    raw = _pdf(["INVOICE 88213", "Amount charged: INR 12400",
                "Date: 2026-07-02", "Merchant: SkyLink Airways"])
    read = documents.extract(raw, "invoice.pdf", "application/pdf")
    assert read.method == "native_pdf", read.note
    assert read.pages == 1
    assert "12400" in read.text
    assert "88213" in read.text


def test_pdf_is_detected_by_magic_bytes_without_a_filename():
    """A browser that uploads with no filename and no content type still gets a
    PDF read as a PDF, rather than decoded as UTF-8 into mojibake."""
    pytest.importorskip("pypdf")
    raw = _pdf(["Amount charged: INR 12400 on 2026-07-02", "Ref 88213"])
    read = documents.extract(raw, "", None)
    assert read.method == "native_pdf"


def test_a_short_receipt_is_read_not_dismissed_as_a_scan():
    """A one-page receipt is short by nature. The sparsity test is per page for
    exactly this reason: a flat floor tuned to reject scans would report a
    perfectly readable receipt as unreadable."""
    pytest.importorskip("pypdf")
    read = documents.extract(_pdf(["Charged twice: 45.00 GBP, ref A7741"]),
                             "receipt.pdf", "application/pdf")
    assert read.method == "native_pdf", read.note
    assert "45.00" in read.text


def test_scanned_pdf_is_reported_as_unread_not_as_empty():
    """The important negative: a scan must not arrive as an empty document.

    A PDF with no text layer yields nothing, and the difference between "this
    document says nothing" and "Agent X could not read this document" is the
    difference between a case with no evidence and a case whose evidence was
    silently dropped.
    """
    pytest.importorskip("pypdf")
    read = documents.extract(_pdf([]), "scan.pdf", "application/pdf")
    assert read.method == "none"
    assert not read.has_text
    assert read.note and "scan" in read.note.lower()


def test_malformed_pdf_does_not_raise():
    read = documents.extract(b"%PDF-1.4\nthis is not a pdf", "broken.pdf",
                             "application/pdf")
    assert read.method == "none"
    assert read.note


def test_unreadable_type_is_labelled_not_silently_empty():
    read = documents.extract(b"\x89PNG\r\n\x1a\n", "photo.png", "image/png")
    assert read.method == "none"
    assert not read.has_text
    assert read.note and "no text layer" in read.note


def test_missing_pypdf_degrades_honestly(monkeypatch):
    """Without the optional dependency the endpoint must still work and say why."""
    import builtins
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "pypdf":
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    read = documents.extract(b"%PDF-1.4\n", "x.pdf", "application/pdf")
    assert read.method == "none"
    assert "pypdf" in (read.note or "")


# ─────────────────────────────────────────────────────────── bounds
def test_oversized_text_is_truncated_and_says_so():
    read = documents.extract(b"a" * (documents.MAX_TEXT_CHARS + 5_000), "big.txt",
                             "text/plain")
    assert read.truncated
    assert len(read.text) <= documents.MAX_TEXT_CHARS
    assert read.note


# ─────────────────────────────────────────────────────────── relevance
CASE = ("I was charged twice for the same order on my credit card by Kartly, "
        "45.00 GBP on 2026-07-11.")


def test_extracted_facts_settle_relevance():
    """A document that yielded facts is evidence, whatever words it uses."""
    r = documents.relevance("402-9938 45.00 GBP 2026-07-11", CASE, "stmt.pdf",
                            facts_found=2)
    assert r.related


def test_a_document_that_names_itself_as_unrelated_is_flagged():
    r = documents.relevance("This is to certify the marksheet of semester 4.",
                            CASE, "marksheet.pdf", facts_found=0)
    assert not r.related
    assert "marksheet" in r.because


def test_shared_wording_counts_as_related():
    r = documents.relevance("Correspondence with Kartly regarding the order "
                            "that was charged twice.", CASE, "email.txt")
    assert r.related
    assert r.shared_terms


def test_an_unrelated_document_is_flagged():
    r = documents.relevance("Recipe for sourdough: flour, water, salt, levain.",
                            CASE, "recipe.txt", facts_found=0)
    assert not r.related


def test_relevance_never_refuses_an_unread_document():
    """No text means no opinion — not a negative one. The file is still evidence."""
    r = documents.relevance("", CASE, "scan.pdf", facts_found=0)
    assert r.related
    assert "not assessed" in r.because


def test_relevance_is_advisory_only():
    """Nothing here may become a refusal: `relevance` reports, the caller stores
    the document regardless. Pinned as a shape assertion because the risk is a
    future caller treating `related=False` as permission to drop evidence."""
    r = documents.relevance("Recipe for sourdough.", CASE, "r.txt")
    assert set(r.as_dict()) == {"related", "because", "shared_terms"}
    assert r.because.strip()


def test_every_extraction_reports_its_method():
    """`as_dict()` is what reaches the API response; it must never omit how the
    text was obtained."""
    for raw, name, mt in [(b"hi there", "a.txt", "text/plain"),
                          (b"\x89PNG", "a.png", "image/png"),
                          (b"%PDF-1.4 broken", "a.pdf", "application/pdf")]:
        d = documents.extract(raw, name, mt).as_dict()
        assert d["method"] in ("utf8", "native_pdf", "none")
        assert "has_text" in d and "chars" in d
