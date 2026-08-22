"""
The Case — Agent X's first-class abstraction.

Everything in this product is a case: one consumer problem, from the sentence the
user typed to a verified outcome, holding its own evidence, facts, policies,
remedies, plan, authorisations, executions, correspondence, deadlines and chain.
There is no other top-level object, and that is deliberate — a consumer agent
built out of features accumulates screens; one built out of cases accumulates
resolutions.

THE STATE MACHINE IS ENFORCED, NOT DOCUMENTED

    OPEN → INVESTIGATING → NEEDS_INPUT ⇄ ACTION_REQUIRED → ACTION_SUBMITTED
         → WAITING_EXTERNAL → FOLLOW_UP_REQUIRED → ESCALATED → RESOLVED

`transition()` refuses an undeclared move. A case cannot jump from OPEN to
RESOLVED because some code path felt confident, and the follow-up scheduler cannot
wake a closed case. Every accepted transition is appended to the case chain with
its reason, so the state history is tamper-evident rather than merely logged.

EVERY CASE HAS AN ERASURE SUBJECT

`subject` is `case:PX-04182`, and it is the key every piece of the case's material
is sealed under. That is what connects a consumer case to Agent X's existing
erasure pipeline: a GDPR Art. 17 request against one dispute destroys that case's
key, the content becomes unrecoverable, and the chain still verifies — because the
chain hashed ciphertext that nobody touched. Per-case rather than per-user because
real erasure requests arrive scoped to an incident, and making a user erase their
whole history to forget one dispute is not a right, it is a penalty.
"""
from __future__ import annotations

from agentx import chain, ids, sealing, store
from agentx.ontology import CASE_STATES, TERMINAL_STATES

# Declared transitions. Anything not listed here is refused.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "OPEN": ("INVESTIGATING", "NEEDS_INPUT", "WITHDRAWN"),
    "INVESTIGATING": ("NEEDS_INPUT", "ACTION_REQUIRED", "RESOLVED",
                      "CLOSED_UNRESOLVED", "WITHDRAWN"),
    "NEEDS_INPUT": ("INVESTIGATING", "ACTION_REQUIRED", "CLOSED_UNRESOLVED",
                    "WITHDRAWN"),
    # WAITING_EXTERNAL and FOLLOW_UP_REQUIRED are reachable from here because an
    # approval hands the case straight back to whatever it was doing: a chase the
    # user has just authorised puts it back in the queue it came from.
    "ACTION_REQUIRED": ("ACTION_SUBMITTED", "WAITING_EXTERNAL", "FOLLOW_UP_REQUIRED",
                        "ESCALATED", "NEEDS_INPUT", "INVESTIGATING",
                        "CLOSED_UNRESOLVED", "WITHDRAWN"),
    "ACTION_SUBMITTED": ("WAITING_EXTERNAL", "RESOLVED", "ESCALATED",
                         "FOLLOW_UP_REQUIRED", "ACTION_REQUIRED",
                         "CLOSED_UNRESOLVED", "WITHDRAWN"),
    "WAITING_EXTERNAL": ("FOLLOW_UP_REQUIRED", "RESOLVED", "ESCALATED",
                         "ACTION_REQUIRED", "CLOSED_UNRESOLVED", "WITHDRAWN"),
    # ACTION_REQUIRED is reachable from both waiting states: a chase that runs out
    # of budget, or a statutory window about to close, both end with Agent X needing
    # a decision from the user. Omitting it stranded cases in FOLLOW_UP_REQUIRED
    # with an escalation nobody could approve.
    "FOLLOW_UP_REQUIRED": ("ACTION_SUBMITTED", "ACTION_REQUIRED", "WAITING_EXTERNAL",
                           "ESCALATED", "RESOLVED", "CLOSED_UNRESOLVED", "WITHDRAWN"),
    "ESCALATED": ("WAITING_EXTERNAL", "ACTION_SUBMITTED", "ACTION_REQUIRED",
                  "RESOLVED", "FOLLOW_UP_REQUIRED", "CLOSED_UNRESOLVED", "WITHDRAWN"),
    "RESOLVED": (),
    "CLOSED_UNRESOLVED": (),
    "WITHDRAWN": (),
}

# What each state means in the user's language. Rendered in the UI verbatim, so
# there is one description of a state rather than one per screen.
STATE_COPY: dict[str, dict] = {
    "OPEN": {"label": "Just opened", "detail": "Agent X has your problem and is starting."},
    "INVESTIGATING": {"label": "Working it out",
                      "detail": "Reading your evidence and checking what applies."},
    "NEEDS_INPUT": {"label": "Needs one thing from you",
                    "detail": "Agent X needs an answer or a document to go further."},
    "ACTION_REQUIRED": {"label": "Ready — needs your go-ahead",
                        "detail": "There is a plan. Review it and approve the action."},
    "ACTION_SUBMITTED": {"label": "Sent",
                         "detail": "Agent X has contacted the company on your behalf."},
    "WAITING_EXTERNAL": {"label": "Waiting on them",
                         "detail": "The company has until its stated deadline to reply."},
    "FOLLOW_UP_REQUIRED": {"label": "They have gone quiet",
                           "detail": "The deadline passed. Agent X is chasing."},
    "ESCALATED": {"label": "Escalated",
                  "detail": "Taken to a higher authority after no result below."},
    "RESOLVED": {"label": "Resolved", "detail": "Confirmed against the company's records."},
    "CLOSED_UNRESOLVED": {"label": "Closed without a result",
                          "detail": "Every available route has been exhausted."},
    "WITHDRAWN": {"label": "Withdrawn", "detail": "You stopped this case."},
}

_COLS = ["id", "workspace", "user_ref", "title", "description", "domain",
         "problem_type", "confidence", "state", "autonomy_level", "risk",
         "resolution", "outcome_summary", "amount_minor", "currency", "job_id",
         "subject", "created_at", "updated_at", "closed_at",
         # Where this case started on the clock it lives on. Zero for every live
         # case; non-zero only when the sandbox clock has already been moved.
         # Without it, elapsed time measured the clock's total displacement
         # rather than the case's own duration -- see 008_case_clock.sql.
         "opened_offset_days"]
_SELECT = ", ".join(_COLS)


def _clock_offset(conn) -> float:
    """The sandbox clock's displacement right now, or 0.0 when there is none.

    Read here rather than imported at module scope: the case machine has no
    business depending on the demo world, and in production the table is simply
    absent. Any failure to read it means "no simulated time", which is the
    correct answer for a live case.
    """
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT offset_days FROM sandbox_clock WHERE id = 'default'")
            row = cur.fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0
    except Exception:
        return 0.0


class InvalidTransition(ValueError):
    """A state move that the machine does not declare."""


# ─────────────────────────────────────────────────────────────────────────────
# lifecycle
# ─────────────────────────────────────────────────────────────────────────────
def create(conn, *, description: str, user_ref: str = "demo-user",
           workspace: str = "default", title: str | None = None,
           autonomy_level: int = 2) -> dict:
    """Open a case. Mints its id, its erasure subject, and its genesis chain row."""
    for _ in range(6):
        cid = ids.case_id()
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM cases WHERE id = %s", (cid,))
            if cur.fetchone() is None:
                break
    else:
        raise RuntimeError("could not mint a unique case id")

    now = ids.now()
    subject = sealing.subject_for(cid)
    row = {"id": cid, "workspace": workspace, "user_ref": user_ref,
           "title": (title or description).strip()[:120],
           "description": description.strip(), "domain": None, "problem_type": None,
           "confidence": None, "state": "OPEN", "autonomy_level": int(autonomy_level),
           "risk": None, "resolution": None, "outcome_summary": None,
           "amount_minor": None, "currency": None, "job_id": None,
           "subject": subject, "created_at": now, "updated_at": now,
           "closed_at": None, "opened_offset_days": _clock_offset(conn)}
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO cases ({_SELECT}) VALUES ({','.join(['%s'] * len(_COLS))})",
            tuple(row[c] for c in _COLS))

    # The narrative itself is sealed: it is the most personal thing in the case
    # and the thing a user is most likely to want erased later.
    chain.append(conn, cid, "case.opened", "HUMAN",
                 {"state": "OPEN", "problem": description.strip(),
                  "user_ref": user_ref, "autonomy": int(autonomy_level)},
                 seal=True, subject=subject, workspace=workspace)
    _link_trust_spine(conn, row)
    return row


def _link_trust_spine(conn, case: dict) -> None:
    """Open an Agent X `jobs` row for this case where the spine exists.

    On CockroachDB this makes a consumer case and an erasure share one trail: the
    same `jobs` table, the same `/verify`, the same certificate machinery. On the
    local engine the spine is absent and the case's own chain stands alone — which
    is why `agentx.store.describe()` reports the difference rather than papering
    over it.
    """
    if store.select_engine() != "cockroachdb":
        return
    try:
        from core.trust import pipeline_job
        job_id = pipeline_job.open_job(conn, kind="document", subject=case["subject"],
                                       workspace=case["workspace"], doc_type="consumer_case",
                                       status="EXTRACTING")
        if job_id:
            with conn.cursor() as cur:
                cur.execute("UPDATE cases SET job_id = %s WHERE id = %s",
                            (job_id, case["id"]))
            case["job_id"] = job_id
            pipeline_job.record(conn, job_id, "case.opened", "HUMAN",
                                {"case_id": case["id"], "kind": "consumer_case"})
    except Exception:
        # The spine is an enhancement to the case chain, never a dependency of it.
        pass


def get(conn, case_id: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_SELECT} FROM cases WHERE id = %s", (case_id,))
        row = cur.fetchone()
    return dict(zip(_COLS, row)) if row else None


def list_cases(conn, *, workspace: str = "default", user_ref: str | None = None,
               state: str | None = None, limit: int = 50) -> list[dict]:
    sql = f"SELECT {_SELECT} FROM cases WHERE workspace = %s"
    params: list = [workspace]
    if user_ref:
        sql += " AND user_ref = %s"
        params.append(user_ref)
    if state == "open":
        sql += " AND state NOT IN ('RESOLVED','CLOSED_UNRESOLVED','WITHDRAWN')"
    elif state == "closed":
        sql += " AND state IN ('RESOLVED','CLOSED_UNRESOLVED','WITHDRAWN')"
    elif state:
        sql += " AND state = %s"
        params.append(state)
    sql += " ORDER BY updated_at DESC LIMIT %s"
    params.append(int(limit))
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [dict(zip(_COLS, r)) for r in cur.fetchall()]


def update(conn, case_id: str, **fields) -> dict | None:
    allowed = {"title", "domain", "problem_type", "confidence", "risk", "resolution",
               "outcome_summary", "amount_minor", "currency", "autonomy_level"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return get(conn, case_id)
    fields["updated_at"] = ids.now()
    sets = ", ".join(f"{k} = %s" for k in fields)
    with conn.cursor() as cur:
        cur.execute(f"UPDATE cases SET {sets} WHERE id = %s",
                    (*fields.values(), case_id))
    return get(conn, case_id)


def transition(conn, case_id: str, to_state: str, *, why: str,
               actor: str = "AGENT") -> dict:
    """Move a case. Refuses anything the machine does not declare."""
    case = get(conn, case_id)
    if not case:
        raise ValueError(f"no such case {case_id}")
    frm = case["state"]
    if to_state not in CASE_STATES:
        raise InvalidTransition(f"{to_state!r} is not a case state")
    if to_state == frm:
        return case
    if to_state not in TRANSITIONS.get(frm, ()):
        raise InvalidTransition(
            f"{case_id}: {frm} → {to_state} is not a declared transition "
            f"(allowed: {list(TRANSITIONS.get(frm, ()))})")

    now = ids.now()
    closed = now if to_state in TERMINAL_STATES else None
    with conn.cursor() as cur:
        cur.execute("UPDATE cases SET state = %s, updated_at = %s, closed_at = %s"
                    " WHERE id = %s", (to_state, now, closed, case_id))
    chain.append(conn, case_id, "case.state", actor,
                 {"from_state": frm, "to_state": to_state, "because": why})
    if to_state in TERMINAL_STATES:
        cancel_followups(conn, case_id, why=f"case closed as {to_state}")
        # One choke point, so no closing path can forget to record and quietly
        # stop the system learning. Recording is best-effort: a failure here must
        # never leave a case stuck un-closed, because the outcome memory is an
        # enhancement to the resolution, not a precondition of it.
        try:
            from agentx import outcomes
            closed = get(conn, case_id)
            rec = outcomes.record(conn, closed, outcome={
                "RESOLVED": "resolved", "CLOSED_UNRESOLVED": "unresolved",
                "WITHDRAWN": "withdrawn"}[to_state])
            if rec:
                chain.append(conn, case_id, "outcome.recorded", "SYSTEM",
                             {"outcome": rec["outcome"], "strategy": rec["strategy"],
                              "chases": rec["chases_needed"],
                              "escalated": rec["escalated"],
                              "reason": "structural outcome recorded for future cases; "
                                        "contains no personal data, so it survives erasure"})
        except Exception:
            pass
    return get(conn, case_id)


def set_autonomy(conn, case_id: str, level: int, *, by: str = "user") -> dict:
    """Change the ceiling on what Agent X may do unattended, and record who did.

    Recorded as a HUMAN act on the chain because it is one: the autonomy level is
    the single most consequential setting in the product, and a receipt has to be
    able to show that the user chose it rather than the system.
    """
    level = max(0, min(4, int(level)))
    with conn.cursor() as cur:
        cur.execute("UPDATE cases SET autonomy_level = %s, updated_at = %s WHERE id = %s",
                    (level, ids.now(), case_id))
    from agentx import governor
    chain.append(conn, case_id, "case.autonomy", "HUMAN",
                 {"level": level, "because": f"set by {by}",
                  "policy_snapshot": governor.policy_snapshot()})
    return get(conn, case_id)


# ─────────────────────────────────────────────────────────────────────────────
# entities
# ─────────────────────────────────────────────────────────────────────────────
def add_entity(conn, case_id: str, *, kind: str, value: str,
               confidence: float | None = None, source: str = "narrative",
               normalized: str | None = None) -> dict:
    from agentx import normalize
    norm = normalized or normalize.canon(value)
    with conn.cursor() as cur:
        cur.execute("SELECT id, confidence FROM case_entities WHERE case_id = %s"
                    " AND kind = %s AND normalized = %s", (case_id, kind, norm))
        existing = cur.fetchone()
        if existing:
            # Keep the more confident reading rather than accumulating duplicates:
            # the same merchant named in a narrative and read off a receipt is one
            # entity with two sources, not two entities.
            if (confidence or 0) > (existing[1] or 0):
                cur.execute("UPDATE case_entities SET value = %s, confidence = %s,"
                            " source = %s WHERE id = %s",
                            (value, confidence, source, existing[0]))
            return {"id": existing[0], "kind": kind, "value": value, "merged": True}
        eid = ids.new("ent")
        cur.execute(
            "INSERT INTO case_entities (id, case_id, kind, value, normalized,"
            " confidence, source, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (eid, case_id, kind, value, norm, confidence, source, ids.now()))
    return {"id": eid, "kind": kind, "value": value, "confidence": confidence,
            "source": source, "merged": False}


def entities(conn, case_id: str) -> list[dict]:
    cols = ["id", "kind", "value", "normalized", "confidence", "source"]
    with conn.cursor() as cur:
        cur.execute("SELECT id, kind, value, normalized, confidence, source"
                    " FROM case_entities WHERE case_id = %s ORDER BY kind, confidence DESC",
                    (case_id,))
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def entity(conn, case_id: str, kind: str) -> dict | None:
    rows = [e for e in entities(conn, case_id) if e["kind"] == kind]
    return rows[0] if rows else None


# ─────────────────────────────────────────────────────────────────────────────
# interpretations
# ─────────────────────────────────────────────────────────────────────────────
def save_interpretations(conn, case_id: str, hypotheses: list, *,
                         replace: bool = True) -> list[dict]:
    """Persist the live hypothesis set. Ruled-out rivals are kept, not deleted.

    Keeping them is what lets a user ask "why not fraud?" and get an answer with a
    number attached, months later. A system that stores only its conclusion cannot
    show its work.
    """
    now = ids.now()
    with conn.cursor() as cur:
        if replace:
            cur.execute("UPDATE case_interpretations SET status = 'RULED_OUT'"
                        " WHERE case_id = %s AND status = 'LIVE'", (case_id,))
        out = []
        for h in hypotheses:
            iid = ids.new("int")
            cur.execute(
                "INSERT INTO case_interpretations (id, case_id, domain, problem_type,"
                " prior, posterior, status, rationale, discriminators, created_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (iid, case_id, h.domain, h.problem_type, h.prior, h.posterior,
                 "LIVE", h.rationale, store.jdump(h.signals), now))
            out.append({"id": iid, "problem_type": h.problem_type,
                        "posterior": h.posterior, "status": "LIVE"})
    return out


def interpretations(conn, case_id: str, live_only: bool = True) -> list[dict]:
    cols = ["id", "domain", "problem_type", "prior", "posterior", "status",
            "rationale", "signals", "created_at"]
    sql = ("SELECT id, domain, problem_type, prior, posterior, status, rationale,"
           " discriminators, created_at FROM case_interpretations WHERE case_id = %s")
    if live_only:
        sql += " AND status = 'LIVE'"
    sql += " ORDER BY posterior DESC"
    with conn.cursor() as cur:
        cur.execute(sql, (case_id,))
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        r["signals"] = store.jload(r["signals"], [])
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# questions
# ─────────────────────────────────────────────────────────────────────────────
def ask(conn, case_id: str, *, question: str, why: str = "", kind: str = "fact",
        options: list | None = None, separates: list | None = None,
        value_bits: float | None = None, qid: str | None = None) -> dict:
    """Queue a question. Idempotent on the discriminator id, so re-running
    understanding does not ask the same thing twice."""
    key = qid or ids.new("q")
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM case_questions WHERE id = %s", (key,))
        if cur.fetchone():
            return {"id": key, "question": question, "existing": True}
        cur.execute(
            "INSERT INTO case_questions (id, case_id, question, why, kind, options,"
            " separates, value_bits, status, created_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'OPEN',%s)",
            (key, case_id, question, why, kind, store.jdump(options or []),
             store.jdump(separates or []), value_bits, ids.now()))
    return {"id": key, "question": question, "kind": kind,
            "options": options or [], "why": why, "value_bits": value_bits}


def open_questions(conn, case_id: str) -> list[dict]:
    cols = ["id", "question", "why", "kind", "options", "separates", "value_bits"]
    with conn.cursor() as cur:
        cur.execute("SELECT id, question, why, kind, options, separates, value_bits"
                    " FROM case_questions WHERE case_id = %s AND status = 'OPEN'"
                    " ORDER BY value_bits DESC NULLS LAST", (case_id,))
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        r["options"] = store.jload(r["options"], [])
        r["separates"] = store.jload(r["separates"], [])
    return rows


def answer(conn, case_id: str, question_id: str, value: str) -> dict:
    """Answer one open question. Refuses a mismatched (case_id, question_id) pair.

    Without that check, looking the question up by its own id alone and writing
    the audit entry under the CALLER-SUPPLIED case_id meant a caller who passed a
    question_id belonging to a different case would still update the right row —
    but attribute it to the wrong case's chain, silently. That was reachable only
    through caller error, but an MCP-connected LLM caller makes caller error to a
    plausible id ordinary, not exotic, so it is checked rather than trusted.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT question, case_id FROM case_questions WHERE id = %s",
                    (question_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"no such question {question_id}")
        if row[1] != case_id:
            raise ValueError(f"question {question_id} belongs to case {row[1]!r}, "
                             f"not {case_id!r}")
        cur.execute("UPDATE case_questions SET answer = %s, status = 'ANSWERED',"
                    " answered_at = %s WHERE id = %s", (value, ids.now(), question_id))
    case = get(conn, case_id)
    chain.append(conn, case_id, "question.answered", "HUMAN",
                 {"question_id": question_id, "question": row[0], "answer": value},
                 seal=True, subject=(case or {}).get("subject"),
                 workspace=(case or {}).get("workspace", "default"))
    return {"id": question_id, "question": row[0], "answer": value}


def obsolete_questions(conn, case_id: str, ids_: list[str]) -> None:
    """Retire questions whose answers no longer matter.

    Called when the hypothesis set collapses: asking a user to separate two
    interpretations that are no longer both live is the most irritating thing a
    consumer agent can do.
    """
    with conn.cursor() as cur:
        for q in ids_:
            cur.execute("UPDATE case_questions SET status = 'OBSOLETE'"
                        " WHERE id = %s AND status = 'OPEN'", (q,))


# ─────────────────────────────────────────────────────────────────────────────
# deadlines and follow-ups
# ─────────────────────────────────────────────────────────────────────────────
def add_deadline(conn, case_id: str, *, kind: str, label: str, due_at: str,
                 source: str = "") -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM deadlines WHERE case_id = %s AND label = %s",
                    (case_id, label))
        if cur.fetchone():
            return {"label": label, "existing": True}
        did = ids.new("dl")
        cur.execute(
            "INSERT INTO deadlines (id, case_id, kind, label, due_at, source, status,"
            " created_at) VALUES (%s,%s,%s,%s,%s,%s,'PENDING',%s)",
            (did, case_id, kind, label, due_at, source, ids.now()))
    return {"id": did, "kind": kind, "label": label, "due_at": due_at, "source": source}


def deadlines(conn, case_id: str, *, as_of: str | None = None) -> list[dict]:
    cols = ["id", "kind", "label", "due_at", "source", "status"]
    with conn.cursor() as cur:
        cur.execute("SELECT id, kind, label, due_at, source, status FROM deadlines"
                    " WHERE case_id = %s ORDER BY due_at ASC", (case_id,))
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    now = as_of or ids.now()
    for r in rows:
        days = ids.days_between(now, r["due_at"])
        r["days_left"] = round(days, 1) if days is not None else None
        r["overdue"] = bool(days is not None and days < 0
                            and r["status"] in ("PENDING", "WARNED"))
    return rows


def schedule_followup(conn, case_id: str, *, kind: str, due_at: str,
                      step_id: str | None = None, require_state: str | None = None,
                      max_attempts: int = 3, detail: str = "") -> dict:
    fid = ids.new("fu")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO followups (id, case_id, step_id, kind, due_at, require_state,"
            " attempt, max_attempts, status, detail, created_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,0,%s,'SCHEDULED',%s,%s)",
            (fid, case_id, step_id, kind, due_at, require_state, max_attempts,
             detail, ids.now()))
    chain.append(conn, case_id, "followup.scheduled", "AGENT",
                 {"kind": kind, "due_at": due_at, "require_state": require_state,
                  "because": detail})
    return {"id": fid, "kind": kind, "due_at": due_at, "status": "SCHEDULED"}


def followups(conn, case_id: str) -> list[dict]:
    cols = ["id", "step_id", "kind", "due_at", "require_state", "attempt",
            "max_attempts", "status", "detail", "fired_at"]
    with conn.cursor() as cur:
        cur.execute("SELECT id, step_id, kind, due_at, require_state, attempt,"
                    " max_attempts, status, detail, fired_at FROM followups"
                    " WHERE case_id = %s ORDER BY due_at ASC", (case_id,))
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def cancel_followups(conn, case_id: str, *, why: str) -> int:
    with conn.cursor() as cur:
        cur.execute("UPDATE followups SET status = 'CANCELLED', detail = %s"
                    " WHERE case_id = %s AND status = 'SCHEDULED'", (why, case_id))
        return cur.rowcount


# ─────────────────────────────────────────────────────────────────────────────
# erasure
# ─────────────────────────────────────────────────────────────────────────────
def forget(conn, case_id: str, *, reason: str = "user request under GDPR Art. 17") -> dict:
    """Crypto-shred one case, and prove it.

    The whole point of the sealed chain: after this, every hash in the case still
    verifies — the ciphertext was hashed and was never touched — and none of the
    personal content can be read again, because the key is gone. The user keeps
    the proof that Agent X acted for them and loses the data inside it.
    """
    case = get(conn, case_id)
    if not case:
        raise ValueError(f"no such case {case_id}")
    before = chain.verify(conn, case_id)
    chain.append(conn, case_id, "case.erasure.requested", "HUMAN",
                 {"because": reason, "chain_length": before.get("rows")})
    receipt = sealing.shred(conn, case["workspace"], case["subject"])
    with conn.cursor() as cur:
        cur.execute("UPDATE cases SET description = %s, title = %s, updated_at = %s"
                    " WHERE id = %s",
                    ("<erased at the user's request>", "<erased>", ids.now(), case_id))
    after = chain.verify(conn, case_id)
    chain.append(conn, case_id, "case.erasure.completed", "SYSTEM",
                 {"because": reason, "key_destroyed": receipt["unrecoverable"],
                  "chain_intact_after": after.get("ok"),
                  "chain_length": after.get("rows")})
    return {"case_id": case_id, "subject": case["subject"], **receipt,
            "chain_intact_before": before.get("ok"),
            "chain_intact_after": chain.verify(conn, case_id).get("ok"),
            "note": "the chain still verifies; the sealed content is unrecoverable"}


def state_copy(state: str) -> dict:
    return STATE_COPY.get(state, {"label": state, "detail": ""})
