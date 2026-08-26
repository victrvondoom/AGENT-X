"""
Eligibility — from "here is what happened" to "here is what you can actually get".

The step most consumer AI skips. A system that identifies a duplicate charge and
then says "you may be entitled to a refund" has done the easy nine-tenths and
stopped before the part with money in it. What a user needs is the ranked list:
which remedies are open, which are blocked and by what, roughly what each is
worth, and which one to try first.

RANKING IS NOT BY VALUE ALONE

A chargeback usually recovers more than a merchant refund and is ranked below it,
because the ordering is:

    1. eligible before blocked           — a route you can take beats one you cannot
    2. reversible before irreversible    — try the thing you can undo first
    3. low risk before high risk         — a chargeback can end a merchant
                                           relationship; asking cannot
    4. higher expected value             — only then does the amount decide

That order encodes the escalation ladder every consumer body recommends: ask the
company, then the platform, then the payment provider, then the regulator. Sorting
by expected value alone would invert it and file a dispute first every time.

WHAT BLOCKS A REMEDY IS NAMED

`blocked_by` is a list of specific, fixable things: a missing document, an
unresolved contradiction, an unestablished jurisdiction. Each one maps to a
question or an upload, which is what turns "not eligible" into a next step rather
than a dead end.
"""
from __future__ import annotations

from agentx import ids, normalize, store
from agentx.ontology import REMEDY_KINDS, ProblemDefinition
from agentx.policy import PolicyFinding, granted_remedies

RISK_ORDER = {"low": 0, "medium": 1, "high": 2}

# Remedies whose value is the disputed amount itself, versus ones whose value
# comes from a statutory table and is computed by the policy layer.
AMOUNT_BACKED = {"merchant_refund", "payment_dispute", "bill_correction"}
PARTIAL_FRACTION = 0.5           # what "partial" is worth when nothing says otherwise


def assess(*, definition: ProblemDefinition, findings: list[PolicyFinding],
           facts: dict, missing_evidence: list[dict],
           blocking_contradictions: list[dict],
           amount_minor: int | None, currency: str | None) -> list[dict]:
    """Rank every remedy this problem type declares. Returns rows ready to persist."""
    supported = granted_remedies(findings)
    unknown_policies = [f for f in findings if f.applies == "unknown"]
    critical_missing = [m for m in missing_evidence if m.get("critical")]

    entitlements = {f.policy.id: (f.entitlement_minor, f.entitlement_currency)
                    for f in findings
                    if f.applies == "yes" and f.entitlement_minor}

    rows: list[dict] = []
    for kind in definition.resolution_strategies:
        meta = REMEDY_KINDS.get(kind, {})
        backing = supported.get(kind, [])
        blocked: list[str] = []

        # `explanation` is the one remedy the planner already treats as always valid
        # (planner.validate() accepts it regardless of a definition's declared
        # strategies) — but only when the definition cites no policy at all, i.e.
        # there is no rule it COULD be backed by. A definition that does cite
        # policies (every domain-specific one) is unaffected: explanation there
        # still needs backing exactly as before.
        no_rule_to_check = kind == "explanation" and not definition.policies
        if not backing and not no_rule_to_check:
            if unknown_policies:
                blocked.append(
                    "no right has been established yet — "
                    + unknown_policies[0].because)
            else:
                blocked.append("no applicable rule supports this remedy on these facts")
        for m in critical_missing:
            blocked.append(f"missing {m['kind'].replace('_', ' ')}: {m.get('why', '')}".strip())
        for c in blocking_contradictions:
            blocked.append(f"unresolved contradiction: {c['detail']}")

        value, value_currency, basis = _value(kind, backing, entitlements,
                                              amount_minor, currency)

        if not blocked:
            eligibility = "eligible"
        elif backing and critical_missing and not blocking_contradictions:
            eligibility = "needs_evidence"
        elif not backing and unknown_policies:
            eligibility = "unknown"
        else:
            eligibility = "ineligible"

        confidence = _confidence(eligibility, backing, critical_missing,
                                 blocking_contradictions, unknown_policies)

        rows.append({
            "kind": kind,
            "title": meta.get("label", kind.replace("_", " ").title()),
            "eligibility": eligibility,
            "confidence": confidence,
            "expected_value_minor": value,
            "currency": value_currency,
            "because": (basis if not blocked else
                        f"{basis} — blocked: {blocked[0]}" if basis else blocked[0]),
            "blocked_by": blocked,
            "supported_by": backing,
            "risk": meta.get("risk", "medium"),
        })

    # Ordering, and why each key is where it is:
    #   1. a route you can take beats one you cannot
    #   2. HIGH-risk routes are demoted outright — a chargeback or a regulator
    #      complaint is the end of the ladder, never the first rung, however much
    #      it is worth
    #   3. among the rest, value decides. This is the correction to an earlier
    #      version that sorted low-risk first and so preferred a discretionary
    #      £92 part-refund over a £350 fixed statutory entitlement.
    #   4. risk breaks a tie between two routes worth the same
    rows.sort(key=lambda r: (
        {"eligible": 0, "needs_evidence": 1, "unknown": 2, "ineligible": 3}[r["eligibility"]],
        1 if r["risk"] == "high" else 0,
        -(r["expected_value_minor"] or 0),
        RISK_ORDER.get(r["risk"], 1),
    ))
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return rows


def _value(kind: str, backing: list[str], entitlements: dict,
           amount_minor: int | None, currency: str | None) -> tuple[int | None, str | None, str]:
    """What this remedy is plausibly worth, and on what basis."""
    for pid in backing:
        if pid in entitlements:
            minor, cur = entitlements[pid]
            return minor, cur, f"fixed entitlement under {pid}"
    if kind in AMOUNT_BACKED and amount_minor:
        return int(amount_minor), currency, "the full disputed amount"
    if kind == "partial_refund" and amount_minor:
        return int(amount_minor * PARTIAL_FRACTION), currency, \
            "a partial refund; the exact share depends on what was used"
    if kind in ("replacement", "repair"):
        return amount_minor, currency, "the value of the goods rather than cash"
    if kind in ("explanation", "cancellation"):
        return None, None, "no money moves; the outcome is the change itself"
    if kind == "goodwill_credit" and amount_minor:
        return int(amount_minor * 0.3), currency, "a discretionary credit, not a right"
    return None, currency, "value depends on what the counterparty offers"


def _confidence(eligibility: str, backing: list[str], missing: list,
                contradictions: list, unknown: list) -> float:
    if eligibility == "ineligible":
        return 0.15
    base = 0.4 + 0.15 * min(3, len(backing))
    base -= 0.15 * min(2, len(missing))
    base -= 0.25 * min(2, len(contradictions))
    base -= 0.05 * min(3, len(unknown))
    return round(max(0.05, min(0.95, base)), 3)


# ─────────────────────────────────────────────────────────────────────────────
# persistence
# ─────────────────────────────────────────────────────────────────────────────
def persist(conn, case_id: str, rows: list[dict]) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM remedies WHERE case_id = %s", (case_id,))
        for r in rows:
            cur.execute(
                "INSERT INTO remedies (id, case_id, kind, title, eligibility, confidence,"
                " expected_value_minor, because, blocked_by, rank, created_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (ids.new("rem"), case_id, r["kind"], r["title"], r["eligibility"],
                 r["confidence"], r.get("expected_value_minor"), r["because"],
                 store.jdump(r.get("blocked_by") or []), r["rank"], ids.now()))
    return rows


def load(conn, case_id: str) -> list[dict]:
    cols = ["id", "kind", "title", "eligibility", "confidence",
            "expected_value_minor", "because", "blocked_by", "rank"]
    with conn.cursor() as cur:
        cur.execute("SELECT id, kind, title, eligibility, confidence,"
                    " expected_value_minor, because, blocked_by, rank FROM remedies"
                    " WHERE case_id = %s ORDER BY rank ASC", (case_id,))
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        r["blocked_by"] = store.jload(r["blocked_by"], [])
    return rows


def persist_policies(conn, case_id: str, findings: list[PolicyFinding]) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM case_policies WHERE case_id = %s", (case_id,))
        for f in findings:
            cur.execute(
                "INSERT INTO case_policies (id, case_id, policy_id, title, authority,"
                " jurisdiction, applies, because, citation, window_days, created_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (ids.new("pol"), case_id, f.policy.id, f.policy.title,
                 f.policy.authority, f.policy.jurisdiction, f.applies, f.because,
                 f.policy.citation, f.policy.window_days, ids.now()))


def load_policies(conn, case_id: str) -> list[dict]:
    cols = ["policy_id", "title", "authority", "jurisdiction", "applies", "because",
            "citation", "window_days"]
    with conn.cursor() as cur:
        cur.execute("SELECT policy_id, title, authority, jurisdiction, applies, because,"
                    " citation, window_days FROM case_policies WHERE case_id = %s"
                    " ORDER BY applies DESC, policy_id", (case_id,))
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def best(rows: list[dict]) -> dict | None:
    """The remedy to try first — the top-ranked eligible one, or None."""
    for r in rows:
        if r["eligibility"] == "eligible":
            return r
    return None


def headline(rows: list[dict], amount_minor: int | None, currency: str | None) -> str:
    """One sentence for the case card. Says what is achievable, not what is possible."""
    top = best(rows)
    if not top:
        blocked = next((r for r in rows if r["eligibility"] == "needs_evidence"), None)
        if blocked:
            return (f"{blocked['title']} looks likely, but Agent X needs "
                    f"{blocked['blocked_by'][0] if blocked['blocked_by'] else 'more'} first.")
        return "Agent X has not established a route to a remedy on these facts yet."
    money = normalize.fmt_money(top.get("expected_value_minor"), currency)
    if top.get("expected_value_minor"):
        return f"{top['title']} — about {money}, on the evidence so far."
    return f"{top['title']} is the route Agent X would take first."
