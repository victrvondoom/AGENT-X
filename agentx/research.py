"""
Research — the procedural half of an answer, retrieved and checked.

`policy.py` establishes what someone is owed. That is the hard half and Agent X
already did it well. The other half is what a person actually has to DO — which
office, in what order, within how many days, quoting what — and until now Agent X
had no corpus to answer it from, so it said nothing.

This module closes that without loosening anything. Its contract with the rest of
the engine is deliberately one-directional:

    reads   the case narrative, its domain, and the policy findings
    writes  cited passages and citation verdicts onto the case
    never   sets a fact, an amount, an entitlement, or an authorisation

A retrieved passage cannot make a remedy eligible, cannot move the governor, and
cannot become a fact in the evidence graph. It can be quoted, attributed, and —
crucially — flagged when it does not actually say what the case wants it to say.
That last part is `knowledge/verify.py`, and it runs here rather than at letter
time so a `conflicting` verdict is on the record before anything is drafted.

Retrieval is deterministic and offline, so this runs identically under
`use_llm=False`, under the SQLite engine, and in `evals/`. A research step that
returned different passages on each run could not be part of a hash-chained case
record, and would not deserve to be.
"""
from __future__ import annotations

from agentx import ids, knowledge

# How many passages a case keeps. Small on purpose: research is context for a
# decision already made deterministically, not a literature review, and every
# extra passage is another chance to cite something tangential.
MAX_PASSAGES = 4


def _query(case: dict) -> str:
    """What to search the corpus for: the user's own account of the problem.

    Deliberately NOT seeded with the titles of the policies that were found to
    apply, which was the obvious first design and was wrong twice over. Policy
    titles are dense in terms the corpus has never seen ("harmonisation of
    turnaround time"), and because an absent term carries maximum IDF, adding
    them drove the coverage score of genuinely relevant passages below the floor
    — a duplicate-charge case retrieved nothing at all. They also drag topic: the
    policy that happens to apply to a cancelled flight is the Montreal
    Convention, so seeding with its title retrieved baggage-liability guidance
    for a case with no baggage in it.

    The narrative is what the person actually said happened, and it is the only
    input here that cannot be wrong about itself. The problem-type label was
    tried as a sharpener and removed for the same reason: when the classifier is
    wrong, appending its label steers retrieval toward the wrong sector with
    confidence. A case reading "the builder has not handed over possession of my
    flat" classifies as `denied_boarding` — Agent X's ontology has no housing —
    and appending that label retrieved airline denied-boarding guidance. Searching
    the narrative alone retrieves the RERA possession complaint route instead.
    """
    return (case.get("description") or case.get("title") or "").strip()


def gather(conn, case: dict, *, findings: list | None = None,
           limit: int = MAX_PASSAGES) -> list[dict]:
    """Retrieve guidance for a case. Returns [] when the corpus has nothing.

    An empty result is a normal outcome, not a degraded one: the corpus covers
    five sectors and most consumer problems fall outside them. Returning nothing
    is how Agent X avoids answering a hotel dispute with airline regulations.

    `findings` is accepted so callers can pass the policy analysis, but it does
    not shape the query — see `_query`. It stays in the signature because the
    caller in `engine.investigate` has it to hand and a future scoring pass may
    legitimately want it for re-ranking rather than for term expansion.
    """
    query = _query(case)
    if not query:
        return []
    # Searched across every sector rather than the one mapped from the case's
    # domain. The mapping (`retrieve.sectors_for_domain`) is still declared and
    # still available to callers who know their sector, but applying it here was
    # measured to change nothing when the classification is right and to do harm
    # when it is wrong: a misclassified housing case had retrieval pinned to
    # airlines and could not reach the guidance that actually fitted it. The
    # absolute-mass floor already supplies the precision the filter was meant to.
    #
    # Over-fetch, then keep the best passage per document. Four passages drawn
    # from two documents look like corroboration and are not: they are the same
    # source quoted twice, and a user reading "4 sources" would be misled.
    candidates = knowledge.search(query, limit=limit * 4)
    if not candidates:
        return []

    # The strongest match fixes the sector, and everything kept must agree with
    # it. This is what replaces the domain-derived filter: it still lets a
    # misclassified case find the right regime (nothing constrains the top hit),
    # while stopping the weaker tail from wandering — a cancelled-flight case was
    # otherwise citing a RERA housing complaint template as its fourth "source",
    # which reads as corroboration and is noise.
    sector = candidates[0].get("sector")

    seen: set[str] = set()
    out: list[dict] = []
    for hit in candidates:
        if hit.get("sector") != sector or hit["doc_id"] in seen:
            continue
        seen.add(hit["doc_id"])
        out.append(hit)
        if len(out) >= limit:
            break
    return out


def check(claims: list[str], passages: list[dict]) -> list:
    """Verify each claim against the retrieved passages."""
    return knowledge.verify_citations([c for c in claims if c and c.strip()],
                                      passages)


def persist(conn, case_id: str, workspace: str, passages: list[dict],
            checks: list | None = None) -> None:
    """Record what was read and how it checked out. Replaces prior research."""
    columns = ("INSERT INTO case_research (id, workspace, case_id, passage_id,"
               " sector, title, authority, citation, score, coverage, rank, claim,"
               " verdict, because, created_at)"
               " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)")
    at = ids.now()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM case_research WHERE case_id = %s", (case_id,))
        for rank, passage in enumerate(passages):
            cur.execute(columns,
                        (ids.new("res"), workspace, case_id, passage["id"],
                         passage.get("sector"), passage.get("title"),
                         passage.get("authority"), passage.get("citation"),
                         passage.get("score"), passage.get("coverage"), rank,
                         None, None, None, at))
        for rank, result in enumerate(checks or []):
            cur.execute(columns,
                        (ids.new("res"), workspace, case_id,
                         (result.source_ids[0] if result.source_ids else ""),
                         None, None, None, None, None, None, rank,
                         result.claim, result.verdict, result.because, at))


def load(conn, case_id: str) -> dict:
    """Research on a case, split into what was read and what was checked."""
    cols = ["passage_id", "sector", "title", "authority", "citation", "score",
            "coverage", "rank", "claim", "verdict", "because"]
    with conn.cursor() as cur:
        cur.execute("SELECT passage_id, sector, title, authority, citation, score,"
                    " coverage, rank, claim, verdict, because FROM case_research"
                    " WHERE case_id = %s ORDER BY rank, passage_id", (case_id,))
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return {
        "sources": [r for r in rows if not r["claim"]],
        "citations": [r for r in rows if r["claim"]],
    }


def summary(passages: list[dict], checks: list | None = None) -> dict:
    """A compact, user-facing account of the research step.

    Deliberately says what was NOT established as loudly as what was. A research
    layer that only reports its successes is how "we found 4 sources" comes to
    mean "your claim is supported" when it means nothing of the kind.
    """
    counts: dict[str, int] = {}
    for result in (checks or []):
        counts[result.verdict] = counts.get(result.verdict, 0) + 1
    return {
        "sources": len(passages),
        "sectors": sorted({p.get("sector") for p in passages if p.get("sector")}),
        "authorities": sorted({p["authority"] for p in passages if p.get("authority")}),
        "verdicts": counts,
        "conflicting": counts.get("conflicting", 0),
        "unverified": counts.get("unsupported", 0) + counts.get("partial", 0),
    }
