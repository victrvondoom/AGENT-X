"""
Contradiction detection — the part that refuses to be helpful.

A receipt saying ₹2,399 and a bank statement saying ₹2,499 is the most valuable
signal in a consumer case, and almost every system built on an LLM destroys it.
Asked to summarise both documents, a model produces one number, and the hundred
rupees that were the entire dispute vanish into fluent prose.

So the rule here is absolute: **when two sources disagree on the same predicate,
Agent X does not choose.** It records both, marks the predicate CONTESTED, drops the
confidence of every claim resting on it, and surfaces the disagreement to the
user in the words of both documents.

That is not timidity. A hundred-rupee gap between a receipt and a statement is
one of three things — a fee, a currency conversion, or the thing the user is
actually owed — and all three are worth more to the user than a smoothed average.

SEVERITY, AND WHY IT DEPENDS ON WHO IS DISAGREEING

  blocking   two issuer documents disagree. Neither can be discounted, so no
             action that depends on the value may proceed until a human rules.
  material   an issuer document disagrees with a user capture. The issuer
             document is likelier, but the gap still has to be explained before
             it goes in a dispute letter.
  minor      two user captures disagree, or the gap is below the noise floor for
             the predicate.

Materiality is per-predicate, not a single epsilon. A one-rupee gap on an amount
matters; a one-minute gap on a timestamp does not.
"""
from __future__ import annotations

from agentx import ids, normalize
from agentx.evidence.graph import facts_for, links_for

# Fractional tolerance below which a numeric difference is noise rather than
# disagreement. Money has none: currency is exact, and "close enough" on an amount
# is how a fee stops being visible.
TOLERANCE = {
    "charge.amount": 0.0, "invoice.total": 0.0, "order.total": 0.0,
    "booking.rate": 0.0, "quoted.amount": 0.0, "refund.amount": 0.0,
    "flight.delay_minutes": 0.05,
    "flight.distance_km": 0.03,
}
DEFAULT_TOLERANCE = 0.01

ISSUER = "issuer_document"

# Predicates whose disagreement stops execution outright, because every remedy in
# the catalogue is denominated in them.
CRITICAL = {"charge.amount", "invoice.total", "order.total", "booking.rate",
            "refund.amount", "order.id", "booking.reference", "charge.date"}


def _trust_of(conn, fact_id: str, cache: dict) -> str:
    if fact_id in cache:
        return cache[fact_id]
    trust = "user_capture"
    with conn.cursor() as cur:
        cur.execute("SELECT evidence_id FROM evidence_links WHERE fact_id = %s LIMIT 1",
                    (fact_id,))
        row = cur.fetchone()
        if row:
            cur.execute("SELECT trust FROM evidence_items WHERE id = %s", (row[0],))
            got = cur.fetchone()
            if got and got[0]:
                trust = got[0]
    cache[fact_id] = trust
    return trust


def _disagree(a: dict, b: dict) -> tuple[bool, str]:
    """Do these two readings of the same predicate actually conflict?"""
    pred = a["predicate"]
    if a["value_num"] is not None and b["value_num"] is not None:
        x, y = float(a["value_num"]), float(b["value_num"])
        if x == y:
            return False, ""
        scale = max(abs(x), abs(y), 1.0)
        tol = TOLERANCE.get(pred, DEFAULT_TOLERANCE)
        if abs(x - y) / scale <= tol:
            return False, ""
        # Different currencies are not a numeric disagreement — they are an
        # incomparable pair, which is its own problem and is reported as one.
        if a.get("unit") and b.get("unit") and a["unit"] != b["unit"]:
            return True, (f"values are in different currencies "
                          f"({a['unit']} vs {b['unit']}), so they cannot be compared")
        return True, (f"{normalize.fmt_money(int(x), a.get('unit')) if 'amount' in pred or 'total' in pred or 'rate' in pred else x}"
                      f" vs "
                      f"{normalize.fmt_money(int(y), b.get('unit')) if 'amount' in pred or 'total' in pred or 'rate' in pred else y}")
    an, bn = (a["value_norm"] or ""), (b["value_norm"] or "")
    if an and bn and an != bn:
        return True, f"{a['value_text']!r} vs {b['value_text']!r}"
    return False, ""


def detect(conn, case_id: str) -> list[dict]:
    """Find every open contradiction in a case and persist the new ones.

    Idempotent: re-running after more evidence arrives adds what is new and leaves
    existing rows (including ones a human has explained) alone.
    """
    rows = facts_for(conn, case_id)
    by_pred: dict[str, list[dict]] = {}
    for f in rows:
        by_pred.setdefault(f["predicate"], []).append(f)

    with conn.cursor() as cur:
        cur.execute("SELECT fact_a, fact_b FROM contradictions WHERE case_id = %s",
                    (case_id,))
        known = {tuple(sorted(r)) for r in cur.fetchall()}

    trust_cache: dict[str, str] = {}
    found: list[dict] = []
    for pred, facts in by_pred.items():
        if len(facts) < 2:
            continue
        for i in range(len(facts)):
            for j in range(i + 1, len(facts)):
                a, b = facts[i], facts[j]
                pair = tuple(sorted((a["id"], b["id"])))
                if pair in known:
                    continue
                conflict, detail = _disagree(a, b)
                if not conflict:
                    continue
                ta, tb = _trust_of(conn, a["id"], trust_cache), _trust_of(conn, b["id"], trust_cache)
                if ta == ISSUER and tb == ISSUER:
                    severity = "blocking" if pred in CRITICAL else "material"
                elif ISSUER in (ta, tb):
                    severity = "material"
                else:
                    severity = "minor"
                row = {
                    "id": ids.new("con"), "case_id": case_id, "predicate": pred,
                    "fact_a": a["id"], "fact_b": b["id"], "severity": severity,
                    "detail": f"{pred}: {detail} ({ta} vs {tb})",
                    "status": "OPEN", "created_at": ids.now(),
                }
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO contradictions (id, case_id, predicate, fact_a, fact_b,"
                        " severity, detail, status, created_at)"
                        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (row["id"], case_id, pred, a["id"], b["id"], severity,
                         row["detail"], "OPEN", row["created_at"]))
                    # Mark BOTH readings contested. Marking only the weaker one
                    # would be a silent adjudication in favour of the other.
                    for fid in (a["id"], b["id"]):
                        cur.execute("UPDATE evidence_facts SET status = 'CONTESTED'"
                                    " WHERE id = %s", (fid,))
                found.append(row)
                known.add(pair)
    return found


def open_contradictions(conn, case_id: str) -> list[dict]:
    cols = ["id", "predicate", "fact_a", "fact_b", "severity", "detail", "status",
            "resolution", "created_at"]
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, predicate, fact_a, fact_b, severity, detail, status, resolution,"
            " created_at FROM contradictions WHERE case_id = %s AND status = 'OPEN'"
            " ORDER BY created_at ASC", (case_id,))
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def contested_predicates(conn, case_id: str) -> set[str]:
    return {c["predicate"] for c in open_contradictions(conn, case_id)}


def blocking(conn, case_id: str) -> list[dict]:
    """Contradictions that must stop execution. The governor consults this."""
    return [c for c in open_contradictions(conn, case_id) if c["severity"] == "blocking"]


def explain(conn, contradiction_id: str, resolution: str, *,
            keep_fact: str | None = None) -> dict:
    """Close a contradiction with a stated reason, optionally naming the winner.

    A resolution is always a sentence, never a silent preference. If a human says
    "the £100 gap is the resort fee", that sentence goes on the case chain and into
    the evidence package, so a reader can disagree with the adjudication rather
    than merely with the number.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT fact_a, fact_b, predicate FROM contradictions WHERE id = %s",
                    (contradiction_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"no such contradiction {contradiction_id}")
        fa, fb, pred = row
        cur.execute("UPDATE contradictions SET status = 'EXPLAINED', resolution = %s"
                    " WHERE id = %s", (resolution, contradiction_id))
        for fid in (fa, fb):
            if keep_fact and fid != keep_fact:
                cur.execute("UPDATE evidence_facts SET status = 'SUPERSEDED' WHERE id = %s",
                            (fid,))
            else:
                cur.execute("UPDATE evidence_facts SET status = 'ACTIVE' WHERE id = %s",
                            (fid,))
    return {"id": contradiction_id, "predicate": pred, "status": "EXPLAINED",
            "resolution": resolution, "kept": keep_fact}


def summary(conn, case_id: str) -> dict:
    rows = open_contradictions(conn, case_id)
    by_sev: dict[str, int] = {}
    for r in rows:
        by_sev[r["severity"]] = by_sev.get(r["severity"], 0) + 1
    return {"open": len(rows), "by_severity": by_sev,
            "blocking": [r["detail"] for r in rows if r["severity"] == "blocking"]}
