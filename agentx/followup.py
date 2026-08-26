"""
The follow-up agent — what makes Agent X an agent rather than a form filler.

A consumer resolution does not end when the first message is sent. It ends days or
weeks later, when someone either pays or refuses, and the single most common reason
a valid claim dies is that nobody chased it. So the product's real work happens
here, on a clock, without the user.

THIS IS NOT A REMINDER SYSTEM

A reminder fires on a date and tells a human to do something. This reads the case,
reads the plan, reads what the counterparty actually said, and decides which of
four things to do:

    chase       the stated response time has elapsed with no decision. Re-contact
                the same counterparty on the same reference.
    escalate    the chase budget is exhausted, or the counterparty refused. Move up
                the ladder the plan declared — and only one rung.
    verify      the counterparty says it is done. Re-read their records to find out
                whether it actually is.
    expire      a statutory or scheme window is closing. Warn while there is still
                time to use it.

Each decision is a function of case state, plan branch, execution history and
deadlines. None of them is a timer.

THREE RULES THAT KEEP IT SAFE

  * A follow-up fires only if the case is still in the state that justified it.
    A case resolved on Tuesday does not get chased on Wednesday because a row said
    so — `require_state` is checked at fire time, not at schedule time.
  * A closed case is never woken. Terminal states cancel outstanding follow-ups.
  * Escalation is still governed. The follow-up agent may DECIDE to escalate; the
    governor decides whether it may act, and at level 2 the answer is that the user
    is asked. Autonomy is not something the scheduler gets to route around.

TIME IS AN ARGUMENT, NOT A GLOBAL

Every entry point takes `as_of`. Production passes nothing and gets the real clock;
the demo passes a moved clock and watches seven days happen in a second. A
scheduler that can be time-travelled from a global is a scheduler nobody can trust,
so there is no global to travel.
"""
from __future__ import annotations

from agentx import case as case_mod
from agentx import chain, ids, planner, store
from agentx.execution import runner

# How many times Agent X will chase before it stops asking nicely.
DEFAULT_CHASE_BUDGET = 2

# How long after a chase before the next one. Escalating cadence: a company that
# ignored one chase will ignore the second one sent the next morning.
CHASE_BACKOFF_DAYS = (3, 5, 7)

# Warn this far ahead of a statutory or scheme window closing. Two weeks is enough
# to actually use a right, which is the only threshold that matters.
DEADLINE_WARNING_DAYS = 14

# Only these deadline kinds can interrupt a case. A merchant's own stated response
# time is not a right expiring — it is the thing the chase follow-up already
# covers, and letting it pull a waiting case into ACTION_REQUIRED cancelled the
# very chase that was scheduled to handle it.
INTERRUPTING_DEADLINES = ("statutory", "scheme")


def due(conn, *, as_of: str | None = None, limit: int = 50,
        case_id: str | None = None) -> list[dict]:
    """Follow-ups whose time has come AND whose case still justifies them."""
    now = as_of or ids.now()
    cols = ["id", "case_id", "step_id", "kind", "due_at", "require_state", "attempt",
            "max_attempts", "detail"]
    sql = ("SELECT f.id, f.case_id, f.step_id, f.kind, f.due_at, f.require_state,"
           " f.attempt, f.max_attempts, f.detail FROM followups f"
           " WHERE f.status = 'SCHEDULED' AND f.due_at <= %s")
    params: list = [now]
    if case_id:
        sql += " AND f.case_id = %s"
        params.append(case_id)
    sql += " ORDER BY f.due_at ASC LIMIT %s"
    params.append(int(limit))
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    live: list[dict] = []
    for r in rows:
        c = case_mod.get(conn, r["case_id"])
        if not c:
            _close(conn, r["id"], "CANCELLED", "the case no longer exists", at=as_of)
            continue
        if c["state"] in ("RESOLVED", "CLOSED_UNRESOLVED", "WITHDRAWN"):
            _close(conn, r["id"], "CANCELLED", f"case is {c['state']}", at=as_of)
            continue
        if r["require_state"] and c["state"] != r["require_state"]:
            _close(conn, r["id"], "CANCELLED",
                   f"case moved to {c['state']}; this follow-up assumed "
                   f"{r['require_state']}", at=as_of)
            continue
        r["case"] = c
        live.append(r)
    return live


def _close(conn, followup_id: str, status: str, detail: str,
           *, at: str | None = None) -> None:
    """Retire a follow-up, stamped with the clock this sweep is running on.

    `fired_at` used to be `ids.now()` unconditionally, which quietly threw away
    the only record of WHEN a follow-up happened in the case's own time. Every
    sandbox case therefore looked like it chased twice, escalated once and
    finished the same second — and `outcomes.record()` learned from exactly that
    stamp, so `typical_days` told the next user this merchant pays out
    immediately. For a live case `at` is wall-clock and nothing changes.
    """
    with conn.cursor() as cur:
        cur.execute("UPDATE followups SET status = %s, detail = %s, fired_at = %s"
                    " WHERE id = %s", (status, detail, at or ids.now(), followup_id))


def run_due(conn, *, as_of: str | None = None, limit: int = 50,
            case_id: str | None = None) -> list[dict]:
    """Fire every follow-up that is due. Returns what each one did."""
    out = []
    for f in due(conn, as_of=as_of, limit=limit, case_id=case_id):
        # Re-read the case immediately before firing. The due list was computed at
        # the top of the sweep, and an earlier follow-up in the same sweep may
        # already have resolved this case — chasing a case that was refunded thirty
        # milliseconds ago is exactly the kind of thing that makes an agent look
        # like it is not paying attention.
        fresh = case_mod.get(conn, f["case_id"])
        if not fresh or fresh["state"] in ("RESOLVED", "CLOSED_UNRESOLVED", "WITHDRAWN"):
            _close(conn, f["id"], "CANCELLED",
                   f"case became {fresh['state'] if fresh else 'absent'} during this sweep", at=as_of)
            continue
        f["case"] = fresh
        try:
            out.append(fire(conn, f, as_of=as_of))
        except runner.NotAuthorized as e:
            # The scheduler wanted to act and the governor said ask. That is the
            # system working, not failing.
            #
            # The follow-up stays SCHEDULED and keeps its due date, so once the
            # user approves, the very next sweep fires it through the scheduler —
            # which is the only path that does the rescheduling, chase-budget and
            # escalation bookkeeping. Closing it here and letting the plan executor
            # run the chase instead skipped all of that and marched the case
            # straight to a receipt while the merchant was still deciding.
            #
            # `require_state` is cleared because the case is about to sit in
            # ACTION_REQUIRED, which is not the state the follow-up was born in.
            with conn.cursor() as cur:
                cur.execute("UPDATE followups SET require_state = NULL, detail = %s"
                            " WHERE id = %s",
                            (f"paused for your approval: {e.verdict.rule}", f["id"]))
            _safe_transition(conn, f["case_id"], "ACTION_REQUIRED",
                             why=f"follow-up needs your approval: {e.prompt}")
            out.append({"followup_id": f["id"], "case_id": f["case_id"],
                        "action": "awaiting_authorization", "prompt": e.prompt})
        except Exception as e:
            _close(conn, f["id"], "FIRED", f"error: {type(e).__name__}: {e}", at=as_of)
            out.append({"followup_id": f["id"], "case_id": f["case_id"],
                        "action": "error", "detail": f"{type(e).__name__}: {e}"})
    return out


def fire(conn, f: dict, *, as_of: str | None = None) -> dict:
    """Do the thing this follow-up exists to do."""
    c = f["case"]
    kind = f["kind"]
    handler = {"chase": _chase, "escalate": _escalate, "verify": _verify,
               "expire": _expire}.get(kind)
    now = as_of or ids.now()
    if handler is None:
        _close(conn, f["id"], "CANCELLED", f"unknown follow-up kind {kind!r}", at=now)
        return {"followup_id": f["id"], "action": "skipped",
                "detail": f"unknown kind {kind!r}"}

    chain.append(conn, c["id"], "followup.fired", "SYSTEM",
                 {"kind": kind, "attempt": f["attempt"] + 1,
                  "max_attempts": f["max_attempts"],
                  "because": f.get("detail") or f"scheduled {kind}"})
    return handler(conn, f, c, now)


# ─────────────────────────────────────────────────────────────────────────────
# handlers
# ─────────────────────────────────────────────────────────────────────────────
def _last_external(conn, case_id: str) -> dict | None:
    """The most recent execution that actually reached a counterparty."""
    for ex in reversed(runner.history(conn, case_id)):
        if ex["external_ref"] and ex["state"] == "COMPLETED":
            return ex
    return None


def _chase(conn, f: dict, c: dict, now: str) -> dict:
    last = _last_external(conn, c["id"])
    if not last:
        _close(conn, f["id"], "CANCELLED",
               "nothing was sent, so there is nothing to chase", at=now)
        return {"followup_id": f["id"], "case_id": c["id"], "action": "skipped",
                "detail": "no external action to chase"}

    attempt = f["attempt"] + 1
    counterparty = _counterparty(conn, c)
    # The scheduler and the plan must not drift. A chase fired here IS the plan's
    # chase step happening, and leaving the step PENDING makes `advance()` offer to
    # do it again — which is how a user ends up approving a chase that already ran.
    if f.get("step_id"):
        planner.set_step_status(conn, f["step_id"], "PENDING", attempts=attempt)
    result = runner.run(conn, case=c, action="follow_up",
                        params={"counterparty": counterparty,
                                "external_ref": last["external_ref"],
                                "case_id": c["id"]},
                        step_id=f.get("step_id"),
                        capability=_cap("follow_up"))

    outcome = result.get("outcome")
    with conn.cursor() as cur:
        cur.execute("UPDATE followups SET attempt = %s WHERE id = %s", (attempt, f["id"]))

    if outcome == "accepted":
        if f.get("step_id"):
            planner.set_step_status(conn, f["step_id"], "DONE", attempts=attempt)
        _close(conn, f["id"], "FIRED", "the counterparty decided in our favour", at=now)
        _safe_transition(conn, c["id"], "ACTION_SUBMITTED",
                         why="the counterparty responded to the chase")
        case_mod.schedule_followup(conn, c["id"], kind="verify",
                                   due_at=ids.in_days(0.5, frm=now),
                                   detail="confirm the decision against their records")
        return {"followup_id": f["id"], "case_id": c["id"], "action": "chased",
                "outcome": "accepted", "next": "verify",
                "detail": result.get("message")}

    if outcome == "refused":
        _close(conn, f["id"], "FIRED", "the counterparty refused", at=now)
        _plan_escalation(conn, c, now, why="the counterparty refused after a chase")
        return {"followup_id": f["id"], "case_id": c["id"], "action": "chased",
                "outcome": "refused", "next": "escalate",
                "detail": result.get("message")}

    # still nothing. Chase again, or move up.
    if attempt >= int(f["max_attempts"]):
        _close(conn, f["id"], "EXHAUSTED",
               f"{attempt} chases with no decision", at=now)
        _plan_escalation(conn, c, now,
                         why=f"no decision after {attempt} chases")
        return {"followup_id": f["id"], "case_id": c["id"], "action": "chased",
                "outcome": "no_response", "next": "escalate",
                "detail": f"chase budget exhausted after {attempt} attempts"}

    gap = CHASE_BACKOFF_DAYS[min(attempt, len(CHASE_BACKOFF_DAYS) - 1)]
    _close(conn, f["id"], "FIRED", "no decision yet; chasing again", at=now)
    case_mod.schedule_followup(
        conn, c["id"], kind="chase", due_at=ids.in_days(gap, frm=now),
        step_id=f.get("step_id"), require_state="WAITING_EXTERNAL",
        max_attempts=f["max_attempts"],
        detail=f"chase {attempt + 1} of {f['max_attempts']}")
    with conn.cursor() as cur:
        cur.execute("UPDATE followups SET attempt = %s WHERE case_id = %s"
                    " AND status = 'SCHEDULED' AND kind = 'chase'",
                    (attempt, c["id"]))
    _safe_transition(conn, c["id"], "WAITING_EXTERNAL",
                     why=f"chased; waiting another {gap} days")
    return {"followup_id": f["id"], "case_id": c["id"], "action": "chased",
            "outcome": "no_response", "next": f"chase again in {gap} days",
            "detail": result.get("message")}


def _escalate(conn, f: dict, c: dict, now: str) -> dict:
    last = _last_external(conn, c["id"])
    plan = planner.active_plan(conn, c["id"])
    step = plan.step("escalate") if plan else None
    target = (step.params.get("to") if step else None) or "platform_support"

    result = runner.run(conn, case=c, action="escalate",
                        params={"counterparty": _counterparty(conn, c),
                                "to": target, "case_id": c["id"],
                                "external_ref": (last or {}).get("external_ref"),
                                "amount_minor": c.get("amount_minor"),
                                "currency": c.get("currency")},
                        step_id=(step.id if step else None),
                        capability=_cap("escalation"))
    _close(conn, f["id"], "FIRED", f"escalated to {target}", at=now)
    _safe_transition(conn, c["id"], "ESCALATED", why=f"escalated to {target}")

    if result.get("outcome") == "accepted":
        case_mod.schedule_followup(conn, c["id"], kind="verify",
                                   due_at=ids.in_days(0.5, frm=now),
                                   detail="confirm the escalated decision")
    else:
        case_mod.schedule_followup(conn, c["id"], kind="chase",
                                   due_at=ids.in_days(5, frm=now),
                                   require_state="ESCALATED", max_attempts=2,
                                   detail=f"chase {target} for a decision")
    return {"followup_id": f["id"], "case_id": c["id"], "action": "escalated",
            "to": target, "outcome": result.get("outcome"),
            "detail": result.get("message")}


def _verify(conn, f: dict, c: dict, now: str) -> dict:
    last = _last_external(conn, c["id"])
    if not last:
        _close(conn, f["id"], "CANCELLED", "there is no action to verify", at=now)
        return {"followup_id": f["id"], "case_id": c["id"], "action": "skipped"}

    v = runner.verify(conn, case=c, execution_id=last["id"])
    if v["verified"] == "verified":
        _close(conn, f["id"], "FIRED", "outcome confirmed against their records", at=now)
        case_mod.update(conn, c["id"], resolution="resolved",
                        outcome_summary=v.get("detail"))
        _safe_transition(conn, c["id"], "RESOLVED",
                         why="confirmed against the counterparty's own records")
        return {"followup_id": f["id"], "case_id": c["id"], "action": "verified",
                "outcome": "resolved", "detail": v.get("detail")}

    if v["verified"] == "contradicted":
        _close(conn, f["id"], "FIRED",
               "the counterparty's records contradict what it told us", at=now)
        _plan_escalation(conn, c, now,
                         why="they said it was done and their records say otherwise")
        return {"followup_id": f["id"], "case_id": c["id"], "action": "verified",
                "outcome": "contradicted", "next": "escalate",
                "detail": v.get("detail")}

    attempt = f["attempt"] + 1
    if attempt >= int(f["max_attempts"]):
        _close(conn, f["id"], "EXHAUSTED", "still not visible in their records", at=now)
        _plan_escalation(conn, c, now, why="the promised credit never appeared")
        return {"followup_id": f["id"], "case_id": c["id"], "action": "verified",
                "outcome": "unverified", "next": "escalate", "detail": v.get("detail")}

    _close(conn, f["id"], "FIRED", "not visible yet; will re-check", at=now)
    case_mod.schedule_followup(conn, c["id"], kind="verify",
                               due_at=ids.in_days(2, frm=now), max_attempts=f["max_attempts"],
                               detail=f"re-check {attempt + 1} of {f['max_attempts']}")
    with conn.cursor() as cur:
        cur.execute("UPDATE followups SET attempt = %s WHERE case_id = %s"
                    " AND status = 'SCHEDULED' AND kind = 'verify'", (attempt, c["id"]))
    return {"followup_id": f["id"], "case_id": c["id"], "action": "verified",
            "outcome": "unverified", "next": "re-check in 2 days",
            "detail": v.get("detail")}


def _expire(conn, f: dict, c: dict, now: str) -> dict:
    """A window is closing. Say so while it can still be used."""
    dls = [d for d in case_mod.deadlines(conn, c["id"], as_of=now)
           if d["status"] == "PENDING"]
    closing = [d for d in dls if d["days_left"] is not None
               and d["days_left"] <= DEADLINE_WARNING_DAYS
               and d["kind"] in INTERRUPTING_DEADLINES]
    _close(conn, f["id"], "FIRED",
           f"{len(closing)} deadline(s) within {DEADLINE_WARNING_DAYS} days", at=now)
    for d in closing:
        chain.append(conn, c["id"], "deadline.warning", "SYSTEM",
                     {"label": d["label"], "due_at": d["due_at"],
                      "elapsed_days": d["days_left"], "kind": d["kind"],
                      "because": "a right expires if it is not used before this date"})
        if d["overdue"]:
            with conn.cursor() as cur:
                cur.execute("UPDATE deadlines SET status = 'MISSED' WHERE id = %s",
                            (d["id"],))
    if closing:
        _safe_transition(conn, c["id"], "ACTION_REQUIRED",
                         why=f"{closing[0]['label']} closes in "
                             f"{closing[0]['days_left']:.0f} days")
    return {"followup_id": f["id"], "case_id": c["id"], "action": "deadline_check",
            "closing": [d["label"] for d in closing]}


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────
def _cap(cap_id: str):
    from agentx import capabilities
    return capabilities.get(cap_id)


def _counterparty(conn, c: dict) -> str | None:
    ent = case_mod.entity(conn, c["id"], "merchant") or \
        case_mod.entity(conn, c["id"], "provider")
    return ent["value"] if ent else None


def _safe_transition(conn, case_id: str, to_state: str, *, why: str) -> None:
    """Transition if the machine allows it; otherwise leave the case where it is.

    The scheduler runs unattended and must never crash a sweep because one case
    was in an unexpected state. The refusal is still recorded, so a state machine
    that is genuinely wrong shows up in the chain rather than in a stack trace.
    """
    try:
        case_mod.transition(conn, case_id, to_state, why=why, actor="SYSTEM")
    except case_mod.InvalidTransition as e:
        chain.append(conn, case_id, "case.state.refused", "SYSTEM",
                     {"to_state": to_state, "because": str(e)[:240]})


def _plan_escalation(conn, c: dict, now: str, *, why: str) -> None:
    """Queue an escalation, or ask the user, depending on the granted autonomy.

    Deliberately does NOT escalate immediately. Escalation is irreversible and
    high risk; the governor would refuse it below level 3 anyway, and scheduling it
    rather than attempting it means the user sees an approval card instead of a
    refusal buried in a log.
    """
    _safe_transition(conn, c["id"], "FOLLOW_UP_REQUIRED", why=why)
    # Chasing is over. Retiring the wait-and-chase steps is what makes `escalate`
    # the next executable step rather than leaving the plan pointing back at a
    # branch the case has already exhausted.
    plan = planner.active_plan(conn, c["id"])
    if plan:
        for key in ("await_response", "chase"):
            st = plan.step(key)
            if st and st.status in ("PENDING", "AWAITING_AUTH"):
                assert st.id is not None, "a step on an active plan is always persisted"
                planner.set_step_status(conn, st.id, "SKIPPED")
    if int(c.get("autonomy_level", 2)) >= 3:
        case_mod.schedule_followup(conn, c["id"], kind="escalate",
                                   due_at=ids.in_days(0.5, frm=now),
                                   require_state="FOLLOW_UP_REQUIRED",
                                   max_attempts=1, detail=why)
    else:
        # An approval REQUEST, not just a note. A recommendation the user has no
        # way to accept is a dead end, and the whole point of stopping here is that
        # the next thing they see is a card with a yes and a no on it.
        plan = planner.active_plan(conn, c["id"])
        step = plan.step("escalate") if plan else None
        target = (step.params.get("to") if step else None) or "the next level"
        existing = [a for a in _pending(conn, c["id"]) if a["action"] == "escalate"]
        if not existing:
            runner.request_authorization(
                conn, c["id"], action="escalate",
                prompt=(f"Agent X wants to escalate this case to "
                        f"{str(target).replace('_', ' ')} because {why}. "
                        f"This cannot be undone once it is filed."),
                step_id=(step.id if step else None), scope="step", level=3)
        chain.append(conn, c["id"], "escalation.recommended", "AGENT",
                     {"because": why, "level": int(c.get("autonomy_level", 2)),
                      "action": "escalate",
                      "reason": "escalation is irreversible and needs your approval "
                                "at this autonomy level"})
        _safe_transition(conn, c["id"], "ACTION_REQUIRED",
                         why="escalation is recommended and needs your approval")


def _pending(conn, case_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT action FROM authorizations WHERE case_id = %s"
                    " AND granted IS NULL", (case_id,))
        return [{"action": r[0]} for r in cur.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# the sweep
# ─────────────────────────────────────────────────────────────────────────────
def sweep(conn, *, as_of: str | None = None, workspace: str = "default") -> dict:
    """One pass of the whole scheduler: deadlines, then due follow-ups.

    Deadlines first, deliberately. A case with a window closing in three days
    should get its warning before Agent X spends its chase budget on a merchant who
    is not going to answer.
    """
    now = as_of or ids.now()
    warned = _sweep_deadlines(conn, now, workspace)
    fired = run_due(conn, as_of=now)
    return {"as_of": now, "deadlines_checked": warned["checked"],
            "deadlines_warned": warned["warned"], "deadlines_missed": warned["missed"],
            "followups_fired": len(fired), "results": fired}


def _sweep_deadlines(conn, now: str, workspace: str) -> dict:
    checked = warned = missed = 0
    for c in case_mod.list_cases(conn, workspace=workspace, state="open", limit=200):
        for d in case_mod.deadlines(conn, c["id"], as_of=now):
            # WARNED deadlines still need the overdue check below; only settled
            # ones are skipped.
            if d["status"] not in ("PENDING", "WARNED"):
                continue
            checked += 1
            if d["days_left"] is None:
                continue
            if d["days_left"] < 0:
                missed += 1
                with conn.cursor() as cur:
                    cur.execute("UPDATE deadlines SET status = 'MISSED' WHERE id = %s",
                                (d["id"],))
                chain.append(conn, c["id"], "deadline.missed", "SYSTEM",
                             {"label": d["label"], "due_at": d["due_at"],
                              "kind": d["kind"],
                              "because": "the window closed before the case reached it"})
            elif (d["status"] == "PENDING" and d["days_left"] <= DEADLINE_WARNING_DAYS
                  and d["kind"] in INTERRUPTING_DEADLINES):
                # Only the PENDING → WARNED transition counts as a new warning. A
                # deadline already sitting at WARNED is re-examined every sweep
                # (it still has to be caught if it later goes overdue, above) but
                # must not re-fire the warning it already raised — the same alarm
                # every morning is an alarm nobody reads by the third day.
                warned += 1
                # WARNED, so the next sweep does not raise the same alarm again.
                # An agent that tells you every morning that the same window is
                # still closing is one you stop reading.
                with conn.cursor() as cur:
                    cur.execute("UPDATE deadlines SET status = 'WARNED' WHERE id = %s",
                                (d["id"],))
                case_mod.schedule_followup(
                    conn, c["id"], kind="expire", due_at=now,
                    max_attempts=1,
                    detail=f"{d['label']} closes in {d['days_left']:.0f} days")
    return {"checked": checked, "warned": warned, "missed": missed}


def timeline(conn, case_id: str) -> list[dict]:
    """Scheduled and fired follow-ups, for the case timeline in the UI."""
    return case_mod.followups(conn, case_id)
