"""
Corpus loading and passage segmentation.

The corpus is a JSONL file of authored regulatory guidance — one record per
document, each carrying the issuing authority and the sector it governs. It is
checked into the repository rather than fetched, because a research layer that
silently changes what it retrieves between runs cannot be part of a reproducible
case record.

Documents are segmented into PASSAGES before indexing. A whole document is the
wrong unit to cite: "RBI Master Direction on Digital Payment Security Controls"
is true of forty paragraphs, only one of which says anything about the ten
working day shadow-reversal deadline. Citing the passage lets the verification
step in `verify.py` check a claim against the specific text that supports it,
rather than against a document large enough to contain almost anything.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache

CORPUS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "corpus.jsonl")

# Passages are built from paragraph boundaries, then packed up to this size.
# Large enough to keep a numbered clause with its qualifying sentence, small
# enough that a citation points somewhere a human can actually check.
TARGET_CHARS = 900
MIN_CHARS = 120


@dataclass(frozen=True)
class Passage:
    """One citable span of regulatory guidance."""
    id: str
    doc_id: str
    sector: str
    title: str
    authority: str | None
    category: str
    text: str
    ordinal: int

    @property
    def citation(self) -> str:
        """How this passage is named when Agent X quotes it."""
        return f"{self.title} — {self.authority}" if self.authority else self.title

    def as_dict(self) -> dict:
        return {"id": self.id, "doc_id": self.doc_id, "sector": self.sector,
                "title": self.title, "authority": self.authority,
                "category": self.category, "citation": self.citation,
                "text": self.text}


def _paragraphs(text: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def _pack(paragraphs: list[str]) -> list[str]:
    """Greedily pack paragraphs up to TARGET_CHARS, never splitting one."""
    out: list[str] = []
    buf = ""
    for para in paragraphs:
        if buf and len(buf) + len(para) + 2 > TARGET_CHARS:
            out.append(buf)
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        out.append(buf)
    # A trailing scrap is folded back rather than emitted as its own passage:
    # a 40-character fragment retrieves badly and cites worse.
    if len(out) > 1 and len(out[-1]) < MIN_CHARS:
        out[-2] = f"{out[-2]}\n\n{out[-1]}"
        out.pop()
    return out


@lru_cache(maxsize=1)
def documents() -> tuple[dict, ...]:
    """Every corpus document, as written. Empty when the corpus is absent."""
    if not os.path.exists(CORPUS_PATH):
        return ()
    out = []
    with open(CORPUS_PATH, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                # A malformed line is skipped rather than failing the import:
                # the research layer is an enhancement, and a corrupt corpus
                # must degrade to "nothing retrieved", never to a broken app.
                continue
    return tuple(out)


@lru_cache(maxsize=1)
def passages() -> tuple[Passage, ...]:
    """The corpus segmented into citable passages."""
    out: list[Passage] = []
    for doc in documents():
        text = (doc.get("text") or "").strip()
        if not text:
            continue
        for ordinal, chunk in enumerate(_pack(_paragraphs(text))):
            out.append(Passage(
                id=f"{doc['id']}#{ordinal}",
                doc_id=doc["id"],
                sector=doc.get("sector") or "general",
                title=doc.get("title") or doc["id"],
                authority=doc.get("authority"),
                category=doc.get("category") or "guidance",
                text=chunk,
                ordinal=ordinal,
            ))
    return tuple(out)


def sectors() -> tuple[str, ...]:
    """Sectors the corpus actually covers — not the sectors we wish it covered."""
    return tuple(sorted({p.sector for p in passages()}))


def stats() -> dict:
    """What the research layer can and cannot answer from, for /health."""
    ps = passages()
    by_sector: dict[str, int] = {}
    for p in ps:
        by_sector[p.sector] = by_sector.get(p.sector, 0) + 1
    return {
        "available": bool(ps),
        "documents": len(documents()),
        "passages": len(ps),
        "sectors": dict(sorted(by_sector.items())),
        "chars": sum(len(p.text) for p in ps),
    }
