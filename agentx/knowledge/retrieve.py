"""
Retrieval — deterministic, explainable, and willing to return nothing.

BM25 over the local corpus. The choice is deliberate rather than a fallback:

  * it is reproducible — the same query returns the same passages forever, which
    is required of anything that ends up on a case's hash chain;
  * it needs no network, no model and no vector service, so `evals/` and the
    offline SQLite engine exercise the same code the deployment runs;
  * it is inspectable — a passage's score decomposes into the terms that earned
    it, so `matched_terms` on a result is the real reason, not a rationalisation.

The floor matters more than the ranking. A retrieval layer that always returns
its best three passages will hand a duplicate-charge case some housing guidance
the moment the corpus lacks anything better, and a downstream model will dutifully
weave it in. `MIN_SCORE` makes "nothing in the corpus addresses this" a real
outcome, and `search()` returning an empty list is a correct, common answer.
"""
from __future__ import annotations

import math
import re
from functools import lru_cache

from agentx.knowledge.corpus import Passage, passages

# Terms carrying no discriminating power in a corpus that is entirely composed of
# consumer-regulatory guidance. "complaint" appears in nearly every passage.
_STOPWORDS = frozenset("""
a an the and or but if then than that this these those of in on at to for with
from by as is are was were be been being do does did have has had will would
shall should may might can could must not no nor so such it its they them their
you your i me my we our us he she his her which who whom what when where why how
about above after again against all also am any because before below between
both during each few further here more most other over own same some too very
""".split())

# BM25 parameters. k1 at the low end of the usual range because regulatory text
# repeats its own key terms heavily and saturation should arrive quickly.
K1 = 1.2
B = 0.75

# A passage must clear this to be returned at all. Calibrated so a query sharing
# only generic vocabulary with the corpus retrieves nothing.
MIN_SCORE = 2.5

# BM25 ranks; it does not decide whether the best passage is worth having. These
# two gates do, and they are why a hotel billing dispute retrieves nothing rather
# than the DGCA passenger charter.
#
# The gate is the ABSOLUTE IDF MASS a passage captures from the query — how much
# of the query's distinctive information actually appears in the passage. Two
# alternatives were tried and are worth recording, because both look right:
#
#   * A raw BM25 floor cannot make the call: one common term in a long query
#     ("hotel", against airline guidance that mentions hotel accommodation) can
#     outscore three specific terms in a short one.
#   * A RATIO of matched mass to total query mass fails on length. A real case
#     narrative is two sentences of proper nouns, reference numbers and amounts
#     that appear nowhere in any corpus; those carry maximum IDF, inflate the
#     denominator, and pushed genuinely relevant passages below the floor — a
#     duplicate-charge case retrieved nothing at all.
#
# Absolute mass is independent of how much the user typed, which is the property
# needed here: adding "booking ref AI4592" to a complaint must not change whether
# the regulation that governs it can be found.
#
# 8.5 is calibrated against this corpus and sits in a genuine but NARROW gap: the
# strongest false hit scores 8.2 (a hotel cancellation reaching for airline
# cancellation guidance) and the weakest true one 8.98. That margin is about 8%,
# which is worth stating plainly rather than dressing up — this threshold
# discriminates well on the cases in the test set and should not be assumed to
# generalise far beyond them.
#
# IDF scales with log(corpus size), so growing the corpus shifts every number
# here. `tests/test_agentx_knowledge.py::test_retrieval_calibration_holds_both_directions`
# pins the separation from both sides, so a corpus change that collapses the gap
# fails loudly instead of quietly degrading into retrieving the nearest thing.
MIN_MATCHED_TERMS = 2
MIN_MATCHED_MASS = 8.5

# How Agent X's problem domains map onto the sectors the corpus covers. Absence
# is meaningful: a domain with no entry searches every sector rather than being
# silently forced into the nearest one.
DOMAIN_SECTORS: dict[str, tuple[str, ...]] = {
    "travel": ("airlines",),
    "finance": ("banking",),
    "billing": ("banking",),
    "services": ("government",),
}


def sectors_for_domain(domain: str | None) -> tuple[str, ...]:
    """Sectors to search for an Agent X domain. Empty tuple means 'all'."""
    return DOMAIN_SECTORS.get((domain or "").strip().lower(), ())


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return [w for w in words if len(w) > 2 and w not in _STOPWORDS]


@lru_cache(maxsize=1)
def _index() -> tuple[tuple[Passage, ...], tuple[dict[str, int], ...],
                      tuple[int, ...], dict[str, int], float]:
    """(passages, per-passage term frequencies, lengths, document frequency, avg length)."""
    ps = passages()
    tfs: list[dict[str, int]] = []
    lengths: list[int] = []
    df: dict[str, int] = {}
    for p in ps:
        terms = tokenize(p.text) + tokenize(p.title) * 2
        tf: dict[str, int] = {}
        for t in terms:
            tf[t] = tf.get(t, 0) + 1
        tfs.append(tf)
        lengths.append(len(terms))
        for t in tf:
            df[t] = df.get(t, 0) + 1
    avg = (sum(lengths) / len(lengths)) if lengths else 0.0
    return ps, tuple(tfs), tuple(lengths), df, avg


def search(query: str, *, sectors: tuple[str, ...] = (), limit: int = 4,
           min_score: float = MIN_SCORE,
           min_mass: float = MIN_MATCHED_MASS) -> list[dict]:
    """Passages relevant to `query`, best first. Empty when the corpus has nothing.

    `sectors` restricts the search; an empty tuple searches everything. Results
    carry their score and the query terms that earned it, so a citation can be
    argued with rather than taken on trust.
    """
    ps, tfs, lengths, df, avg = _index()
    if not ps or avg <= 0:
        return []

    q_terms = tokenize(query)
    if not q_terms:
        return []
    # Duplicate query terms shouldn't multiply a passage's score.
    q_unique = list(dict.fromkeys(q_terms))

    total = len(ps)
    allowed = frozenset(sectors) if sectors else None

    # Standard BM25 IDF, floored at zero so a term appearing in more than half the
    # corpus cannot subtract from a passage's score. Computed once per query term.
    idfs = {t: max(0.0, math.log(1 + (total - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5)))
            for t in q_unique}
    available_mass = sum(idfs.values())

    scored: list[tuple[float, int, list[str], float]] = []
    for i, passage in enumerate(ps):
        if allowed is not None and passage.sector not in allowed:
            continue
        tf, length = tfs[i], lengths[i]
        score = 0.0
        matched_mass = 0.0
        matched: list[str] = []
        for term in q_unique:
            freq = tf.get(term, 0)
            if not freq:
                continue
            idf = idfs[term]
            norm = freq * (K1 + 1) / (freq + K1 * (1 - B + B * length / avg))
            score += idf * norm
            matched_mass += idf
            matched.append(term)

        if (score >= min_score and len(matched) >= MIN_MATCHED_TERMS
                and matched_mass >= min_mass):
            coverage = (matched_mass / available_mass) if available_mass else 0.0
            scored.append((score, i, matched, matched_mass, coverage))

    # Sort by score, then passage id, so ties resolve identically on every run.
    scored.sort(key=lambda r: (-r[0], ps[r[1]].id))

    out = []
    for score, i, matched, matched_mass, coverage in scored[:limit]:
        result = ps[i].as_dict()
        result["score"] = round(score, 3)
        result["matched_mass"] = round(matched_mass, 3)
        # Reported for inspection, not gated on — see the note above on why a
        # ratio cannot be the gate.
        result["coverage"] = round(coverage, 3)
        result["matched_terms"] = matched
        out.append(result)
    return out
