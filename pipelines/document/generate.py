"""
Document generation and read-back.

DWS offers POST /processor/generate_pdf, and with a key that is what should build
the final artefact. Without one this module writes a small, valid PDF locally so
the self-verifying loop has a REAL file to re-read — the loop is worthless if
"re-extract the signed document" is secretly a dict compared against itself.

So the round-trip here is genuine: values are serialised into a PDF content stream,
the bytes are hashed, and reading them back parses the stream rather than
remembering what was written. Corrupt a byte in the file and the read-back changes.
"""
from __future__ import annotations

import hashlib
import re

PAGE_W, PAGE_H = 595, 842


def _esc(s: str) -> str:
    """Escape a string for a PDF literal. Backslash first, or it double-escapes."""
    return (s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)"))


def build_pdf(title: str, fields: dict[str, str]) -> bytes:
    """Render approved fields into a minimal, valid PDF.

    Field values are written as `key: value` lines in the content stream, which is
    what read_pdf_fields parses back. A marker line pins the field block so the
    reader cannot mistake page furniture for data.
    """
    lines = [f"AGENT X / TrustDoc -- {title}", "", "--- FIELDS ---"]
    lines += [f"{k}: {v}" for k, v in sorted(fields.items())]
    lines += ["--- END FIELDS ---"]

    ops = ["BT", "/F1 11 Tf", f"60 {PAGE_H - 70} Td", "14 TL"]
    for ln in lines:
        ops.append(f"({_esc(ln)}) Tj")
        ops.append("T*")
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1", "replace")

    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        (f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 {PAGE_W} {PAGE_H}]"
         f"/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>").encode(),
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
        b"<</Length " + str(len(stream)).encode() + b">>\nstream\n" + stream + b"\nendstream",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<</Size {len(objs) + 1}/Root 1 0 R>>\n"
            f"startxref\n{xref_at}\n%%EOF\n").encode()
    return bytes(out)


_TJ = re.compile(rb"\((?:\\.|[^\\()])*\)\s*Tj")


def read_pdf_fields(pdf: bytes) -> dict[str, str]:
    """Parse field values back OUT of a PDF by reading its content stream.

    Deliberately does not trust anything held in memory: it locates the stream,
    pulls every text-showing operator, and reconstructs the key/value block. This
    is what makes the self-verify loop real rather than ceremonial.
    """
    start = pdf.find(b"stream\n")
    end = pdf.find(b"\nendstream", start)
    if start == -1 or end == -1:
        return {}
    stream = pdf[start + len(b"stream\n"):end]

    texts: list[str] = []
    for m in _TJ.finditer(stream):
        lit = m.group(0)
        lit = lit[lit.index(b"(") + 1: lit.rindex(b")")]
        s = (lit.replace(rb"\(", b"(").replace(rb"\)", b")")
                .replace(rb"\\", b"\\")).decode("latin-1")
        texts.append(s)

    fields: dict[str, str] = {}
    inside = False
    for t in texts:
        if t == "--- FIELDS ---":
            inside = True
            continue
        if t == "--- END FIELDS ---":
            break
        if inside and ": " in t:
            k, v = t.split(": ", 1)
            fields[k] = v
    return fields


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
