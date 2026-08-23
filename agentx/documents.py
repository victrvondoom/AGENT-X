"""
Document intelligence — reading what was uploaded, and saying so honestly.

Until now `/cases/{id}/upload` decoded UTF-8 and nothing else. That was correct
about its own limits — it labelled a PDF as having no text layer rather than
pretending — but it meant the most common piece of consumer evidence in
existence, a PDF invoice, arrived as bytes Agent X could hash and not read.

This module adds the reading. Two properties are kept from the endpoint it
serves, because they are the reason that endpoint was trustworthy while it could
not read anything:

  * The method used is always reported. `native_pdf` and `utf8` are different
    claims about how much to trust the text, and a caller is told which happened.
  * Failure is a stated outcome, never a silent empty string. An encrypted PDF, a
    scan with no text layer, and a missing extraction library are three different
    reasons for having no text, and they are reported as three different reasons.

There is deliberately no vision-model OCR here. The source project had one, and
it is genuinely useful for scans — but it makes reading a document depend on a
network call to a hosted model, which would put a non-deterministic step inside
evidence extraction. Agent X's evidence path is reproducible under `use_llm=False`
and that is load-bearing for `evals/`. A scanned PDF is therefore reported as a
scan with no text layer, which is true and checkable, rather than transcribed by
a model whose output no one can reproduce.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass

# Uploads with a text layer Agent X can decode directly.
TEXT_TYPES = {"text/plain", "text/markdown", "text/csv", "message/rfc822",
              "application/json", "text/html", ""}
TEXT_SUFFIXES = (".txt", ".md", ".csv", ".eml", ".json", ".log", ".html")

PDF_TYPES = {"application/pdf", "application/x-pdf"}
PDF_SUFFIXES = (".pdf",)

# When a PDF's extractable text is this sparse it is page furniture — a header, a
# page number — rather than content, which is the signature of a scan.
#
# Scaled by page count rather than fixed, because the two errors are asymmetric
# and both are real. A flat floor high enough to reject a ten-page scan (whose
# stray text can run to dozens of characters) also rejects a one-line receipt,
# reporting a document Agent X read perfectly well as unreadable. Sparsity per
# page is the property that actually distinguishes them.
MIN_PDF_CHARS_FLOOR = 20
MIN_PDF_CHARS_PER_PAGE = 15


def _min_pdf_text(pages: int) -> int:
    return max(MIN_PDF_CHARS_FLOOR, MIN_PDF_CHARS_PER_PAGE * max(pages, 1))

# A defensive ceiling on how much of an upload is turned into text. A crafted PDF
# can expand enormously relative to its byte size; evidence extraction downstream
# is linear in text length, and an unbounded document is an easy way to make one
# upload occupy a worker indefinitely.
MAX_TEXT_CHARS = 400_000
MAX_PDF_PAGES = 80


@dataclass(frozen=True)
class Extraction:
    """What Agent X managed to read out of an upload, and how."""
    text: str
    method: str          # utf8 | native_pdf | none
    pages: int = 0
    note: str | None = None
    truncated: bool = False

    @property
    def has_text(self) -> bool:
        return bool(self.text.strip())

    def as_dict(self) -> dict:
        return {"method": self.method, "pages": self.pages,
                "chars": len(self.text), "has_text": self.has_text,
                "truncated": self.truncated, "note": self.note}


def _looks_like(name: str, media_type: str | None,
                types: set[str], suffixes: tuple[str, ...]) -> bool:
    return ((media_type or "") in types) or name.lower().endswith(suffixes)


def _clean(text: str) -> str:
    """Collapse the whitespace PDF extraction produces without joining words."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _truncate(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_TEXT_CHARS:
        return text, False
    return text[:MAX_TEXT_CHARS], True


def extract_pdf(raw: bytes) -> Extraction:
    """Text from a PDF's own text layer. No OCR, and no pretence of it."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return Extraction(
            text="", method="none",
            note="PDF text extraction is unavailable on this deployment "
                 "(pypdf is not installed). The file is stored and hashed as "
                 "evidence, and no text was read from it.")

    try:
        reader = PdfReader(io.BytesIO(raw))
    except Exception:
        # A malformed or hostile PDF must not take the request down. It is
        # evidence either way — it is stored and hashed; it is simply unread.
        return Extraction(text="", method="none",
                          note="This PDF could not be parsed. It is stored and "
                               "hashed as evidence, and no text was read from it.")

    if getattr(reader, "is_encrypted", False):
        # An empty-password decrypt covers the common "protected but not secret"
        # case. A genuinely encrypted file stays unread and says so.
        try:
            if not reader.decrypt(""):
                raise ValueError("still encrypted")
        except Exception:
            return Extraction(text="", method="none",
                              note="This PDF is password-protected. It is stored "
                                   "and hashed as evidence, and no text was read "
                                   "from it.")

    pages = reader.pages[:MAX_PDF_PAGES]
    chunks = []
    for page in pages:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:
            # One unreadable page should not discard the rest of the document.
            chunks.append("")

    text = _clean("\n\n".join(c for c in chunks if c.strip()))
    text, truncated = _truncate(text)
    page_count = len(reader.pages)

    if len(text) < _min_pdf_text(len(pages)):
        return Extraction(
            text=text, method="none", pages=page_count,
            note=("This PDF has no usable text layer — it is almost certainly a "
                  "scan or a photograph. It is stored and hashed as evidence, and "
                  "no facts were extracted from it. Agent X does not transcribe "
                  "images, so nothing here has been guessed at."))

    note = None
    if page_count > MAX_PDF_PAGES:
        note = (f"Only the first {MAX_PDF_PAGES} of {page_count} pages were read.")
    elif truncated:
        note = "This document was long; only the first part was read."
    return Extraction(text=text, method="native_pdf", pages=page_count,
                      note=note, truncated=truncated)


def extract(raw: bytes, filename: str = "", media_type: str | None = None) -> Extraction:
    """Read an upload. Always reports the method, never invents text."""
    name = filename or "upload"

    if _looks_like(name, media_type, PDF_TYPES, PDF_SUFFIXES) or raw[:5] == b"%PDF-":
        return extract_pdf(raw)

    if _looks_like(name, media_type, TEXT_TYPES, TEXT_SUFFIXES):
        text, truncated = _truncate(_clean(raw.decode("utf-8", errors="replace")))
        return Extraction(text=text, method="utf8", truncated=truncated,
                          note=("This document was long; only the first part was "
                                "read." if truncated else None))

    return Extraction(
        text="", method="none",
        note=(f"{media_type or 'this file type'} has no text layer Agent X can "
              f"read. It is stored and hashed as evidence, and no facts were "
              f"extracted from it."))


# ─────────────────────────────────────────────────────────────────────────────
# relevance — does this document have anything to do with the case?
# ─────────────────────────────────────────────────────────────────────────────
# Uploading the wrong file is ordinary user behaviour, and the failure it causes
# is quiet: an unrelated document contributes no facts, so the case simply stays
# short of evidence and the user, who believes they have supplied it, waits.
#
# This is ADVISORY and never blocks. Agent X does not know what a user's evidence
# is for — a bank statement can be the whole case in a duplicate-charge dispute
# and pure background in a delayed-flight one — so the honest move is to say what
# was noticed and store the document either way. Refusing an upload on a keyword
# score would be worse than the problem it solves.
_IRRELEVANT_SIGNALS = (
    "internship certificate", "completion certificate", "marksheet",
    "admit card", "hall ticket", "offer letter", "curriculum vitae",
    "salary slip", "payslip", "birth certificate", "degree certificate",
    "convocation", "caste certificate", "domicile certificate",
    "marriage certificate", "death certificate",
)

_SHARED_TERMS_EXPECTED = 2


@dataclass(frozen=True)
class Relevance:
    """Whether an upload appears to bear on the case. Advisory only."""
    related: bool
    because: str
    shared_terms: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {"related": self.related, "because": self.because,
                "shared_terms": list(self.shared_terms)}


def relevance(text: str, case_description: str, filename: str = "",
              facts_found: int = 0) -> Relevance:
    """Does this document look like it belongs to this case?

    Three signals, cheapest first: a document that names itself as something
    unrelated, the facts Agent X actually managed to extract, and vocabulary
    shared with the user's own account of the problem. Extracted facts outrank
    the vocabulary check — a receipt that yielded an amount and a date is
    evidence whatever words it happens to use.
    """
    if not text.strip():
        return Relevance(related=True,
                         because="no text was read, so relevance was not assessed")

    haystack = re.sub(r"\s+", " ", f"{filename} {text[:3000]}").lower()
    for signal in _IRRELEVANT_SIGNALS:
        if signal in haystack:
            return Relevance(
                related=False,
                because=(f"this looks like a {signal}, which is unlikely to bear on "
                         f"the problem described. It has been stored as evidence "
                         f"anyway — if it is relevant, nothing has been lost."))

    if facts_found:
        return Relevance(related=True,
                         because=f"{facts_found} fact(s) were extracted from it")

    # Import here: `knowledge` pulls in the corpus index, and `documents` is
    # imported by the API at startup where that cost is not yet warranted.
    from agentx.knowledge.retrieve import tokenize
    doc_terms = set(tokenize(text[:5000]))
    case_terms = set(tokenize(case_description or ""))
    shared = tuple(sorted(doc_terms & case_terms))[:8]
    if len(shared) >= _SHARED_TERMS_EXPECTED:
        return Relevance(related=True,
                         because="it shares specific wording with the case",
                         shared_terms=shared)

    return Relevance(
        related=False, shared_terms=shared,
        because=("nothing in this document matches the problem described, and no "
                 "facts could be extracted from it. It has been stored as evidence; "
                 "if it is relevant, describe how and Agent X will reconsider."))
