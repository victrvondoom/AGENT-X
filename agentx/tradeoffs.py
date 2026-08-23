"""
Trade-offs — the routes that are genuinely different, and the ones that are just worse.

`eligibility.assess()` ranks remedies into a single ordered list and
`eligibility.best()` takes the top one. That is the right default: most people
want to be told what to do. But a single ordering has to collapse three things a
consumer weighs differently — how much money comes back, how likely it is to work,
and how much can go wrong — into one number, and the weights that do the
collapsing are ours, not theirs.

Someone who needs £40 this week and someone who wants the maximum they are owed
are not choosing badly relative to each other. They are choosing differently, and
a ranked list cannot represent that.

So this module answers a different question: **which options are actually worth
considering?** A remedy is worth considering when nothing else beats it on
everything at once. That is Pareto optimality, and it is the honest shape of a
choice with several objectives — it narrows without deciding.

    frontier    remedies no other remedy beats on every objective
    dominated   remedies that ARE beaten on every objective, with the winner named

The second half matters as much as the first. "This route is strictly worse than
that one, here is which and why" is a stronger thing to tell someone than silently
dropping it, and it is checkable.

THREE OBJECTIVES, ALL ALREADY MEASURED

  value        `expected_value_minor` — what the remedy is worth, maximise
  confidence   `confidence`           — how sure Agent X is, maximise
  risk         `REMEDY_KINDS[..]risk` — declared per remedy, minimise

Nothing here is invented. Speed is a conspicuous omission: elapsed-time figures
exist only per counterparty and only from sandbox scenarios on a simulated clock
(`outcomes.prior_for`), so a per-remedy "days" objective would be a fabricated
number wearing a real one's clothes. When a real measurement of it exists, it
becomes a fourth objective and nothing else here changes.
"""
from __future__ import annotations

from agentx.ontology import REMEDY_KINDS

# Higher is worse. Ordinal, not a score — the gaps between these are not claimed
# to be equal, and Pareto comparison only ever asks "worse, same, or better".
RISK_ORDER = {"low": 0, "medium": 1, "high": 2}

# What each objective is called when Agent X explains a trade-off to a person.
OBJECTIVES = {
    "value": "how much you get back",
    "confidence": "how likely it is to work",
    "risk": "how much can go wrong",
}


def _objectives(remedy: dict) -> tuple[float, float, float]:
    """(value, confidence, safety) — all three as 'higher is better'."""
    value = float(remedy.get("expected_value_minor") or 0)
    confidence = float(remedy.get("confidence") or 0.0)
    risk = REMEDY_KINDS.get(remedy.get("kind") or "", {}).get("risk", "medium")
    safety = -float(RISK_ORDER.get(risk, 1))
    return value, confidence, safety


def dominates(a: dict, b: dict) -> bool:
    """Does `a` beat `b` on every objective, and strictly beat it on one?

    Equal remedies do not dominate each other, which is what keeps two genuinely
    equivalent routes both on the frontier instead of one arbitrarily winning.
    """
    oa, ob = _objectives(a), _objectives(b)
    return all(x >= y for x, y in zip(oa, ob)) and any(x > y for x, y in zip(oa, ob))


def _why_better(a: dict, b: dict) -> str:
    """Plain-language account of how `a` beats `b`."""
    oa, ob = _objectives(a), _objectives(b)
    names = ["gets you more back", "is more likely to work", "carries less risk"]
    better = [n for n, x, y in zip(names, oa, ob) if x > y]
    same = len(better) < 3
    lead = " and ".join(better) if better else "matches it everywhere"
    return (f"{a.get('title') or a.get('kind')} {lead}"
            + (", and is no worse on anything else" if same and better else ""))


def analyse(remedies: list[dict]) -> dict:
    """Split remedies into the ones worth considering and the ones that are not.

    Only remedies that are actually open are compared. A blocked remedy is not a
    worse choice, it is not a choice — putting it on a frontier would suggest the
    user could pick it.
    """
    open_remedies = [r for r in (remedies or []) if r.get("eligibility") == "eligible"]

    frontier: list[dict] = []
    dominated: list[dict] = []
    for candidate in open_remedies:
        beaten_by = next((other for other in open_remedies
                          if other is not candidate and dominates(other, candidate)),
                         None)
        if beaten_by is None:
            frontier.append(candidate)
        else:
            dominated.append({
                "kind": candidate.get("kind"),
                "title": candidate.get("title"),
                "beaten_by": beaten_by.get("kind"),
                "because": _why_better(beaten_by, candidate),
            })

    return {
        "frontier": [_present(r, frontier) for r in _ordered(frontier)],
        "dominated": dominated,
        "considered": len(open_remedies),
        "is_a_real_choice": len(frontier) > 1,
        "note": _note(len(frontier), len(open_remedies)),
    }


def _ordered(frontier: list[dict]) -> list[dict]:
    """Highest value first, then confidence — a stable presentation order.

    This is NOT a ranking. Every remedy on the frontier is undominated; the order
    exists so the same case renders the same way twice.
    """
    return sorted(frontier, key=lambda r: (-float(r.get("expected_value_minor") or 0),
                                           -float(r.get("confidence") or 0.0),
                                           r.get("kind") or ""))


def _present(remedy: dict, frontier: list[dict]) -> dict:
    """One frontier option, with what it is uniquely best at."""
    value, confidence, safety = _objectives(remedy)
    best_at = []
    if all(value >= _objectives(o)[0] for o in frontier) and len(frontier) > 1:
        best_at.append("most money back")
    if all(confidence >= _objectives(o)[1] for o in frontier) and len(frontier) > 1:
        best_at.append("most likely to work")
    if all(safety >= _objectives(o)[2] for o in frontier) and len(frontier) > 1:
        best_at.append("least risk")
    return {
        "kind": remedy.get("kind"),
        "title": remedy.get("title"),
        "expected_value_minor": remedy.get("expected_value_minor"),
        "confidence": remedy.get("confidence"),
        "risk": REMEDY_KINDS.get(remedy.get("kind") or "", {}).get("risk", "medium"),
        "best_at": best_at,
        "because": remedy.get("because"),
    }


def _note(frontier: int, considered: int) -> str:
    if considered == 0:
        return "No remedy is open yet, so there is nothing to choose between."
    if frontier <= 1:
        return ("One route is better than every alternative on every measure, so "
                "there is no trade-off to weigh here.")
    return (f"{frontier} routes are genuinely different: each one beats the others "
            f"on something. Which is right depends on whether you care more about "
            f"the amount, the odds, or the risk — so Agent X is not choosing for you.")
