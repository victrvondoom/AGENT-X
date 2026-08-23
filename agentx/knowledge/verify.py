"""
Citation verification — does the source actually say what we claim it says?

A cited regulation is the most load-bearing sentence a consumer agent produces
and the least checked. The usual arrangement has one model assert a rule and a
second model read the same context and agree, which detects a fabrication only
when the second model happens not to share the first one's error.

This is deterministic instead. A claim is checked against the text of the
passages actually retrieved for it, and the result is one of four verdicts —
never a boolean, because "we could not confirm this" and "this is contradicted"
are different facts about the world and a user is entitled to be told which.

    verified      the source text supports the claim
    partial       the source is on topic but does not establish the claim
    unsupported   nothing retrieved supports it
    conflicting   the source states something incompatible with the claim

The numeric rule is the part that matters. Consumer-regulation claims turn on
figures — ten working days, INR 10,000, 400% of base fare — and a word-overlap
check passes "reversal within 30 working days" against a source that says ten,
because every word except the number matches. So a figure asserted in a claim
must appear in the supporting text; a figure present in the claim and absent from
an otherwise strongly-matching source is what `conflicting` means here. That
single rule is the difference between citation checking and citation theatre.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict

from agentx.knowledge.retrieve import tokenize

VERDICTS = ("verified", "partial", "unsupported", "conflicting")

# Share of a claim's distinctive words that must appear in the source.
VERIFY_RATIO = 0.7
PARTIAL_RATIO = 0.4

# Below this many distinctive words a claim is too generic to check either way.
# "RTI", "GST", "the Consumer Protection Act" carry no proposition to verify, and
# flagging them as unsupported would train users to ignore the flag.
MIN_TERMS = 3

_NUMBER = re.compile(r"\b\d[\d,]*(?:\.\d+)?\b")

# Figures that are units or ordinals rather than regulatory quantities. Matching
# on these produces noise: nearly every passage contains a "1" or a "2".
_TRIVIAL_NUMBERS = frozenset({"0", "1", "2", "3", "4"})


@dataclass(frozen=True)
class CitationCheck:
    """One claim, checked against one body of retrieved source text."""
    claim: str
    verdict: str
    because: str
    matched_ratio: float
    source_ids: tuple[str, ...] = ()
    unmatched_figures: tuple[str, ...] = ()

    @property
    def safe_to_state(self) -> bool:
        """Whether this claim may be presented to a counterparty as established."""
        return self.verdict == "verified"

    def as_dict(self) -> dict:
        d = asdict(self)
        d["source_ids"] = list(self.source_ids)
        d["unmatched_figures"] = list(self.unmatched_figures)
        d["safe_to_state"] = self.safe_to_state
        return d


def _figures(text: str) -> set[str]:
    """Regulatory quantities in a piece of text, normalised for comparison."""
    out = set()
    for raw in _NUMBER.findall(text or ""):
        value = raw.replace(",", "").rstrip(".")
        if value.endswith(".0"):
            value = value[:-2]
        if value and value not in _TRIVIAL_NUMBERS:
            out.add(value)
    return out


def verify_citation(claim: str, sources: list[dict]) -> CitationCheck:
    """Check one claim against the passages retrieved to support it."""
    if not claim or not claim.strip():
        return CitationCheck(claim=claim or "", verdict="unsupported",
                             because="empty claim", matched_ratio=0.0)
    if not sources:
        return CitationCheck(claim=claim, verdict="unsupported",
                             because="nothing was retrieved for this claim",
                             matched_ratio=0.0)

    corpus_text = "\n\n".join((s.get("text") or "") for s in sources)
    source_ids = tuple(s.get("id", "") for s in sources if s.get("id"))
    lowered = corpus_text.lower()

    # Verbatim containment settles it outright.
    if claim.lower().strip() in lowered:
        return CitationCheck(claim=claim, verdict="verified",
                             because="the source contains this claim verbatim",
                             matched_ratio=1.0, source_ids=source_ids)

    terms = list(dict.fromkeys(tokenize(claim)))
    if len(terms) < MIN_TERMS:
        return CitationCheck(
            claim=claim, verdict="partial",
            because="too few distinctive terms to verify or refute",
            matched_ratio=0.0, source_ids=source_ids)

    hits = sum(1 for t in terms if t in lowered)
    ratio = hits / len(terms)

    # Figures asserted by the claim that the source never states.
    claim_figures = _figures(claim)
    source_figures = _figures(corpus_text)
    unmatched = tuple(sorted(claim_figures - source_figures))

    if unmatched and ratio >= PARTIAL_RATIO:
        # The wording lines up but a number does not. This is the case a
        # word-overlap check passes and a reader would be misled by.
        return CitationCheck(
            claim=claim, verdict="conflicting",
            because=("the source is on point but does not state "
                     f"{', '.join(unmatched)} — check this figure against the source"),
            matched_ratio=round(ratio, 3), source_ids=source_ids,
            unmatched_figures=unmatched)

    if ratio >= VERIFY_RATIO:
        return CitationCheck(claim=claim, verdict="verified",
                             because=f"{hits} of {len(terms)} distinctive terms appear in the source",
                             matched_ratio=round(ratio, 3), source_ids=source_ids)

    if ratio >= PARTIAL_RATIO:
        return CitationCheck(
            claim=claim, verdict="partial",
            because=("the source is on topic but does not establish this claim "
                     f"({hits} of {len(terms)} terms)"),
            matched_ratio=round(ratio, 3), source_ids=source_ids)

    return CitationCheck(
        claim=claim, verdict="unsupported",
        because=f"only {hits} of {len(terms)} distinctive terms appear in the retrieved source",
        matched_ratio=round(ratio, 3), source_ids=source_ids)


def verify_citations(claims: list[str], sources: list[dict]) -> list[CitationCheck]:
    """Check every claim against the same retrieved sources, preserving order."""
    return [verify_citation(c, sources) for c in claims]
