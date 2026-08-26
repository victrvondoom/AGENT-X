"""
Outcome memory — the resolution engine learning from cases it already handled.

Every closed case leaves one structural record behind (`case_outcomes`), and
every new case reads those records back before it plans. The result is an agent
whose strategy against a specific company improves with experience, and can say
exactly which prior cases taught it what.

WHAT IS LEARNED, AND WHAT IS NOT

Learned: which remedy actually paid out, how many chases it took before anyone
answered, whether escalation was necessary, how long it ran, and which cited
rights preceded a settlement. All of it structural.

Not learned — and unlearnable here by construction — is anything about the
person. `case_outcomes` has no column for an amount, a reference, a narrative or
a user (see `db/migrations/007_outcomes.sql`). That is what lets this survive
the right to erasure intact: a shredded case's contents are unrecoverable, and
"Kartly settles duplicate-charge claims only after escalation" is still true,
because it was never personal data in the first place.

THREE RULES THIS LAYER OBEYS

  1. **A prior informs; it never authorises.** Nothing here can widen what the
     governor allows, skip an authorisation, or make a remedy eligible that the
     policy layer did not establish. It adjusts *how* a permitted plan is shaped
     — the wait, the chase budget, which eligible remedy is tried first.
  2. **Thin evidence is reported as thin.** One prior case is an anecdote and is
     labelled `weak`; the confidence a prior carries is a function of how many
     cases agree and how consistently, never of how convenient the conclusion is.
  3. **Sandbox and live are never pooled.** A prior learned against a simulated
     company cannot shape a plan against a real one, or the whole
     mode-is-carried-not-inferred discipline collapses at exactly the point it
     would matter most.
"""
from __future__ import annotations

from agentx import ids, normalize, store

# Below this many agreeing cases, a prior is an anecdote: reported, visible in
# the UI and the chain, but never allowed to change a plan.
MIN_CASES_TO_ACT = 2

# Ratio at or above which a settlement counts as "paid in full" for the purpose
# of deciding whether a strategy worked. Not 1.0: a rounding difference or a
# retained fee should not flip a successful outcome into a failed one.
FULL_RECOVERY = 0.95


def _norm(counterparty: str | None) -> str | None:
    return normalize.canon(counterparty) if counterparty else None


# ─────────────────────────────────────────────────────────────────────────────
# recording
# ─────────────────────────────────────────────────────────────────────────────
def record(conn, case: dict, *, outcome: str) -> dict | None:
    """Write the structural record for a case that has just closed.

    Called from `case.transition()` the moment a case enters a terminal state —
    one choke point, so no closing path can forget to record and quietly stop
    the system learning.
    """
    from agentx import eligibility
    from agentx.execution import runner

    case_id = case["id"]
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM case_outcomes WHERE case_id = %s", (case_id,))
        if cur.fetchone():
            return None                      # already recorded; closing is idempotent

    executions = runner.history(conn, case_id)
    external = [e for e in executions if e.get("external_ref")]
    chases = sum(1 for e in executions if e["action"] == "follow_up")
    escalations = [e for e in executions if e["action"] == "escalate"]

    recovered = 0
    cited: list[str] = []
    for e in executions:
        if e.get("verified") == "verified":
            d = e.get("data") or {}
            recovered += int(d.get("posted_minor") or d.get("amount_approved_minor") or 0)
        for r in ((e.get("data") or {}).get("cited_rights") or []):
            if r not in cited:
                cited.append(r)

    claimed = case.get("amount_minor") or 0
    ratio = round(min(1.0, recovered / claimed), 3) if claimed else None

    plan_strategy = None
    with conn.cursor() as cur:
        cur.execute("SELECT strategy FROM plans WHERE case_id = %s"
                    " ORDER BY version DESC LIMIT 1", (case_id,))
        row = cur.fetchone()
        if row:
            plan_strategy = row[0]

    counterparty = None
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM case_entities WHERE case_id = %s"
                    " AND kind IN ('merchant','provider') ORDER BY confidence DESC LIMIT 1",
                    (case_id,))
        row = cur.fetchone()
        if row:
            counterparty = row[0]

    # Mode is taken from what actually ran, never assumed. A case with no
    # external action at all has no mode to report.
    modes = {e.get("provider_mode") for e in external if e.get("provider_mode")}
    mode = modes.pop() if len(modes) == 1 else ("mixed" if modes else None)

    days = _elapsed_days(conn, case)

    row = {
        "id": ids.new("out"), "workspace": case.get("workspace", "default"),
        "case_id": case_id, "counterparty": _norm(counterparty),
        "problem_type": case.get("problem_type"), "domain": case.get("domain"),
        "strategy": plan_strategy, "outcome": outcome,
        "recovery_ratio": ratio, "chases_needed": chases,
        "escalated": bool(escalations),
        "escalated_to": ((escalations[-1].get("data") or {}).get("to")
                         if escalations else None),
        "days_to_close": round(days, 2) if days is not None else None,
        "cited_rights": store.jdump(cited), "provider_mode": mode,
        "created_at": ids.now(),
    }
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO case_outcomes (id, workspace, case_id, counterparty,"
            " problem_type, domain, strategy, outcome, recovery_ratio, chases_needed,"
            " escalated, escalated_to, days_to_close, cited_rights, provider_mode,"
            " created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            tuple(row[k] for k in
                  ("id", "workspace", "case_id", "counterparty", "problem_type",
                   "domain", "strategy", "outcome", "recovery_ratio", "chases_needed",
                   "escalated", "escalated_to", "days_to_close", "cited_rights",
                   "provider_mode", "created_at")))
    return row


# ─────────────────────────────────────────────────────────────────────────────
# reading back
# ─────────────────────────────────────────────────────────────────────────────
_COLS = ["id", "case_id", "counterparty", "problem_type", "domain", "strategy",
         "outcome", "recovery_ratio", "chases_needed", "escalated", "escalated_to",
         "days_to_close", "cited_rights", "provider_mode", "created_at"]


def history(conn, *, workspace: str = "default", counterparty: str | None = None,
            problem_type: str | None = None, mode: str | None = None,
            limit: int = 50) -> list[dict]:
    sql = (f"SELECT {', '.join(_COLS)} FROM case_outcomes WHERE workspace = %s")
    params: list = [workspace]
    if counterparty:
        sql += " AND counterparty = %s"
        params.append(_norm(counterparty))
    if problem_type:
        sql += " AND problem_type = %s"
        params.append(problem_type)
    if mode:
        sql += " AND provider_mode = %s"
        params.append(mode)
    sql += " ORDER BY created_at DESC LIMIT %s"
    params.append(int(limit))
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = [dict(zip(_COLS, r)) for r in cur.fetchall()]
    for r in rows:
        r["cited_rights"] = store.jload(r["cited_rights"], [])
        r["escalated"] = bool(r["escalated"])
    return rows


def _elapsed_days(conn, case: dict) -> float | None:
    """How long the case took, measured on the clock the CASE lived on.

    Wall-clock subtraction of `closed_at - created_at` was wrong for every
    sandbox case, and wrongness here is not cosmetic: `days_to_close` is what
    `prior_for()` averages into `typical_days`, which the UI shows to a person
    deciding whether it is worth pursuing a claim. A demo case that chased twice
    and escalated once — seven simulated days of waiting on a merchant —
    recorded 0.0 days, because all seven happened inside one real second. The
    system would then have told the next user that this merchant pays out the
    same day.

    So take the latest moment the case actually reached. Follow-ups fire at
    `as_of`, which in sandbox is the moved clock; executions finish at the same
    stamp. Whichever is furthest ahead of `created_at` is the elapsed time the
    case experienced, whether the days were real or simulated. For a live case
    every stamp is wall-clock and this reduces to exactly the old computation.
    """
    started = case.get("created_at")
    if not started:
        return None
    marks = [case.get("closed_at")]
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(fired_at) FROM followups WHERE case_id = %s"
                    " AND fired_at IS NOT NULL", (case["id"],))
        row = cur.fetchone()
        if row:
            marks.append(row[0])
        cur.execute("SELECT MAX(finished_at) FROM executions WHERE case_id = %s"
                    " AND finished_at IS NOT NULL", (case["id"],))
        row = cur.fetchone()
        if row:
            marks.append(row[0])
    spans = [d for d in (ids.days_between(started, m) for m in marks if m)
             if d is not None]
    # Both ends must be read on the same clock. `created_at` is wall-clock, but a
    # sandbox case's later stamps sit at wall-clock + the clock's displacement,
    # so a case opened after the clock had already been moved would otherwise
    # inherit every day advanced before it existed.
    opened_at_offset = float(case.get("opened_offset_days") or 0.0)
    if not spans:
        elapsed = ids.days_between(started, ids.now())
        return max(elapsed - opened_at_offset, 0.0) if elapsed is not None else None
    # `max`, not the closing stamp: a case can be closed by a sweep whose `as_of`
    # is the moved clock, or by a user action at wall-clock time after the
    # sandbox has run ahead. The furthest point is the one the case reached.
    return max(max(spans) - opened_at_offset, 0.0)


def prior_for(conn, *, workspace: str = "default", counterparty: str | None,
              problem_type: str | None, mode: str | None = None) -> dict:
    """What experience says about this counterparty and this problem type.

    Returns a prior even when there is nothing to go on — `cases: 0`,
    `actionable: False` — because a caller that has to distinguish "no history"
    from "history says nothing useful" should not have to infer it from a None.
    """
    rows = history(conn, workspace=workspace, counterparty=counterparty,
                   problem_type=problem_type, mode=mode)
    empty = {"cases": 0, "actionable": False, "counterparty": counterparty,
             "problem_type": problem_type, "basis": [],
             "note": "no prior cases against this counterparty for this problem"}
    if not rows:
        return empty

    resolved = [r for r in rows if r["outcome"] == "resolved"]
    paid_in_full = [r for r in resolved
                    if (r["recovery_ratio"] or 0) >= FULL_RECOVERY]
    needed_escalation = [r for r in resolved if r["escalated"]]
    chase_counts = [r["chases_needed"] for r in resolved]
    # `is not None`, not truthiness: a case that closed the same day has
    # days_to_close == 0.0, which is real data, not a missing value.
    day_counts = [r["days_to_close"] for r in resolved
                  if r["days_to_close"] is not None]

    strategies: dict[str, int] = {}
    for r in resolved:
        if r["strategy"]:
            strategies[r["strategy"]] = strategies.get(r["strategy"], 0) + 1
    best_strategy = max(strategies, key=lambda k: strategies[k]) if strategies else None

    rights: dict[str, int] = {}
    for r in resolved:
        for c in r["cited_rights"]:
            rights[c] = rights.get(c, 0) + 1

    n = len(rows)
    return {
        "cases": n,
        # Two agreeing cases is the floor for changing a plan. Below it the prior
        # is still returned and still shown — it just cannot steer anything.
        "actionable": n >= MIN_CASES_TO_ACT,
        "strength": "strong" if n >= 4 else ("moderate" if n >= MIN_CASES_TO_ACT else "weak"),
        "counterparty": counterparty, "problem_type": problem_type,
        "resolved": len(resolved),
        "success_rate": round(len(resolved) / n, 2),
        "paid_in_full_rate": round(len(paid_in_full) / len(resolved), 2) if resolved else None,
        "escalation_rate": round(len(needed_escalation) / len(resolved), 2) if resolved else None,
        "typical_chases": (round(sum(chase_counts) / len(chase_counts))
                           if chase_counts else None),
        "typical_days": round(sum(day_counts) / len(day_counts), 1) if day_counts else None,
        "best_strategy": best_strategy,
        "rights_that_worked": sorted(rights, key=lambda k: rights[k], reverse=True)[:3],
        "basis": [r["case_id"] for r in rows],      # auditable provenance
        "note": _summarise(n, resolved, needed_escalation, best_strategy),
    }


def _summarise(n: int, resolved: list, escalated: list, strategy: str | None) -> str:
    if not resolved:
        return (f"{n} prior case(s) against this counterparty, none resolved — "
                f"experience suggests this is a hard one")
    bits = [f"{len(resolved)} of {n} prior case(s) resolved"]
    if strategy:
        bits.append(f"usually via {strategy.replace('_', ' ')}")
    if escalated and len(escalated) / len(resolved) >= 0.5:
        bits.append("and usually only after escalation")
    elif not escalated:
        bits.append("without needing to escalate")
    return ", ".join(bits)


# ─────────────────────────────────────────────────────────────────────────────
# systemic signal
# ─────────────────────────────────────────────────────────────────────────────
# A single consumer being stonewalled is a dispute. The same company stonewalling
# every claim of the same kind is a pattern, and it is the one thing an
# individual complainant can never see on their own — they only ever have their
# own case. This is the closest thing in the product to a genuinely new
# capability rather than an automated one.
SYSTEMIC_MIN_CASES = 3
SYSTEMIC_ESCALATION_RATE = 0.75


def systemic_signal(conn, *, workspace: str = "default", counterparty: str | None,
                    problem_type: str | None) -> dict | None:
    """Does this counterparty refuse this problem class as a matter of course?

    Deliberately conservative: three cases minimum and a three-quarters
    escalation rate before the word "pattern" is used at all, because the claim
    is a serious one and the whole point of the evidence discipline elsewhere in
    this system is not making serious claims cheaply.
    """
    rows = history(conn, workspace=workspace, counterparty=counterparty,
                   problem_type=problem_type)
    if len(rows) < SYSTEMIC_MIN_CASES:
        return None
    resolved = [r for r in rows if r["outcome"] == "resolved"]
    if not resolved:
        return {"pattern": "never_settles", "cases": len(rows),
                "detail": f"{len(rows)} cases of this kind, none resolved",
                "basis": [r["case_id"] for r in rows]}
    rate = sum(1 for r in resolved if r["escalated"]) / len(resolved)
    if rate < SYSTEMIC_ESCALATION_RATE:
        return None
    return {
        "pattern": "settles_only_on_escalation",
        "cases": len(rows), "escalation_rate": round(rate, 2),
        "detail": (f"{int(rate * 100)}% of resolved cases of this kind against this "
                   f"counterparty settled only after escalation — first-line refusal "
                   f"looks like policy, not circumstance"),
        "basis": [r["case_id"] for r in rows],
    }
