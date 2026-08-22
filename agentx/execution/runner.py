"""
The executor — Action → Evidence → Verification, enforced rather than intended.

Every external action in Agent X passes through here, and here is where the
product's central claim is either true or empty. The claim is:

    Agent X never says an action succeeded. It says what it attempted, what the
    external system returned, what evidence it captured, and — separately, later,
    by re-reading that system — whether the state it wanted actually exists.

So `run()` cannot report success. It reports `COMPLETED` with `verified =
"unverified"`, and only `verify()` — which calls back out to the provider and
reads the world again — can move that to `"verified"`. A refund is not refunded
because a portal said "approved"; it is refunded when the credit is in the ledger.
The two are different columns and different points in time, and conflating them is
the single most common lie an agent product tells.

THE ORDER OF OPERATIONS IS THE SAFETY MODEL

    1. resolve the provider          — no provider, no step, no pretending
    2. governor assessment           — risk, autonomy, contradictions, ceilings
    3. authorisation check           — an explicit grant, stored with its prompt
    4. write REQUESTED               — before anything leaves, so a crash is visible
    5. call the provider
    6. capture evidence              — what came back, stored and hashed
    7. write COMPLETED / FAILED / REFUSED
    8. append every transition to the case chain

Step 4 matters more than it looks. Writing the record before the call means a
process that dies mid-action leaves a REQUESTED row with no outcome — visible,
investigable, and honest — instead of no trace of an action that may well have
landed.
"""
from __future__ import annotations

from agentx import chain, ids, governor, normalize, store
from agentx.evidence import graph as egraph
from agentx.execution import actions as A
from agentx.execution import providers


class NotAuthorized(RuntimeError):
    """The action needs an authorisation that has not been granted."""

    def __init__(self, verdict, prompt: str):
        self.verdict = verdict
        self.prompt = prompt
        super().__init__(verdict.rule)


def _write(conn, row: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO executions (id, case_id, step_id, action, provider,"
            " provider_mode, request, state, result, external_ref, evidence_id,"
            " verified, error, requested_at, started_at, finished_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (row["id"], row["case_id"], row.get("step_id"), row["action"],
             row["provider"], row["provider_mode"], row.get("request"), row["state"],
             row.get("result"), row.get("external_ref"), row.get("evidence_id"),
             row.get("verified", "unverified"), row.get("error"),
             row["requested_at"], row.get("started_at"), row.get("finished_at")))


def _advance(conn, execution_id: str, **fields) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k} = %s" for k in fields)
    with conn.cursor() as cur:
        cur.execute(f"UPDATE executions SET {sets} WHERE id = %s",
                    (*fields.values(), execution_id))


def _redact(params: dict) -> dict:
    """What goes in the request record.

    The message body is stored as a communication row and as evidence, not
    duplicated into every execution record — and anything that looks like a
    credential never lands here at all.
    """
    drop = {"body", "password", "token", "api_key", "secret", "otp", "cvv"}
    return {k: v for k, v in (params or {}).items()
            if k not in drop and not isinstance(v, (bytes, bytearray))}


def active_authorization(conn, case_id: str, *, step_id: str | None = None,
                         action: str | None = None) -> dict | None:
    """A granted, unexpired authorisation covering this action, if one exists.

    Checked in order of specificity: a grant for THIS step beats one for the
    action class, which beats a case-wide standing grant. Specific grants win so a
    user who approved one refund has not, by that act, approved every refund.
    """
    now = ids.now()
    cols = ["id", "scope", "action", "prompt", "granted", "granted_by", "level",
            "constraints", "expires_at", "step_id"]
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, scope, action, prompt, granted, granted_by, level, constraints,"
            " expires_at, step_id FROM authorizations WHERE case_id = %s"
            " AND granted = %s AND (expires_at IS NULL OR expires_at > %s)"
            " ORDER BY requested_at DESC", (case_id, True, now))
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        if step_id and r["step_id"] == step_id:
            return r
    for r in rows:
        if action and r["scope"] == "action" and r["action"] == action:
            return r
    for r in rows:
        if r["scope"] == "case":
            return r
    return None


def request_authorization(conn, case_id: str, *, action: str, prompt: str,
                          step_id: str | None = None, scope: str = "step",
                          level: int | None = None,
                          constraints: dict | None = None,
                          expires_in_days: float | None = 14) -> dict:
    """Record that Agent X is asking. The prompt is stored verbatim.

    Storing the rendered sentence, not a code, is what makes the grant evidence:
    months later a reader can see exactly what the user was told before they said
    yes, rather than a key into a template that has since been edited.
    """
    # An identical undecided request already on the table is the SAME request. A
    # scheduler that retries every sweep would otherwise stack a new approval card
    # on the user's screen every few minutes for one pending decision.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, prompt FROM authorizations WHERE case_id = %s AND action = %s"
            " AND granted IS NULL AND (step_id = %s OR (step_id IS NULL AND %s IS NULL))"
            " ORDER BY requested_at DESC LIMIT 1",
            (case_id, action, step_id, step_id))
        existing = cur.fetchone()
    if existing:
        return {"id": existing[0], "case_id": case_id, "action": action,
                "scope": scope, "prompt": existing[1], "granted": None,
                "step_id": step_id, "existing": True}

    aid = ids.new("auth")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO authorizations (id, case_id, step_id, scope, action, prompt,"
            " granted, granted_by, level, constraints, requested_at, expires_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,NULL,NULL,%s,%s,%s,%s)",
            (aid, case_id, step_id, scope, action, prompt, level,
             store.jdump(constraints or {}), ids.now(),
             ids.in_days(expires_in_days) if expires_in_days else None))
    chain.append(conn, case_id, "authorization.requested", "AGENT",
                 {"action": action, "scope": scope, "prompt": prompt,
                  "authorization_id": aid, "level": level})
    return {"id": aid, "case_id": case_id, "action": action, "scope": scope,
            "prompt": prompt, "granted": None, "step_id": step_id}


def decide_authorization(conn, authorization_id: str, *, granted: bool,
                         by: str = "user") -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT case_id, action, prompt, scope FROM authorizations WHERE id = %s",
                    (authorization_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"no such authorization {authorization_id}")
        case_id, action, prompt, scope = row
        cur.execute("UPDATE authorizations SET granted = %s, granted_by = %s,"
                    " decided_at = %s WHERE id = %s",
                    (bool(granted), by, ids.now(), authorization_id))
    chain.append(conn, case_id, "authorization.decided", "HUMAN",
                 {"authorization_id": authorization_id, "action": action,
                  "granted": bool(granted), "prompt": prompt, "scope": scope,
                  "decided_by": by})
    return {"id": authorization_id, "granted": bool(granted), "case_id": case_id,
            "action": action}


# ─────────────────────────────────────────────────────────────────────────────
# execution
# ─────────────────────────────────────────────────────────────────────────────
def run(conn, *, case: dict, action: str, params: dict,
        step_id: str | None = None, capability=None,
        actor: str = "AGENT", require_authorization: bool | None = None) -> dict:
    """Execute one action. Returns the execution record.

    Raises NotAuthorized when a grant is needed and absent — deliberately an
    exception rather than a return value, because a caller that forgets to check a
    boolean would proceed, and this is the one place in the system where
    proceeding by accident is unacceptable.
    """
    spec = A.spec(action)
    if spec is None:
        raise ValueError(f"unknown action {action!r}; the vocabulary is {sorted(A.ACTIONS)}")

    case_id = case["id"]
    counterparty = params.get("counterparty") or params.get("merchant")
    family = (getattr(capability, "provider_family", None) or spec.family)
    provider = (providers.resolve(family, counterparty=counterparty, action=action)
                if family else None)

    # 1. no provider, no step. The registry answers, not optimism.
    if family and (provider is None or not provider.supports(action)):
        rec = _record(conn, case_id, step_id, action,
                      provider.id if provider else f"unavailable:{family}",
                      getattr(provider, "mode", "none"), params,
                      state="FAILED",
                      error=f"no provider can {action} for {counterparty or family}")
        chain.append(conn, case_id, "action.unavailable", "SYSTEM",
                     {"action": action, "provider": rec["provider"],
                      "reason": rec["error"]})
        return rec

    # 2. the governor.
    verdict = governor.assess(
        action=action, capability=capability,
        case_level=int(case.get("autonomy_level", 2)),
        risk=(getattr(capability, "risk", None) or spec.risk),
        confidence=case.get("confidence"),
        amount_minor=params.get("amount_minor"),
        currency=params.get("currency"),
        blocking_contradictions=int(params.get("_blocking_contradictions", 0)),
        counterparty=counterparty)

    if not verdict.allow:
        rec = _record(conn, case_id, step_id, action,
                      provider.id if provider else "internal",
                      getattr(provider, "mode", "internal"), params,
                      state="REFUSED", error=verdict.explain)
        chain.append(conn, case_id, "action.refused", "SYSTEM",
                     {"action": action, "decision": verdict.rule,
                      "because": verdict.explain, "risk": verdict.risk,
                      "policy_snapshot": governor.policy_snapshot()})
        return rec

    # 3. authorisation.
    needs_auth = verdict.requires_authorization if require_authorization is None \
        else require_authorization
    auth = None
    if needs_auth:
        auth = active_authorization(conn, case_id, step_id=step_id, action=action)
        if not auth:
            prompt = verdict.prompt or governor._prompt(
                action, params.get("amount_minor"), params.get("currency"),
                counterparty, verdict.reversible)
            request_authorization(conn, case_id, action=action, prompt=prompt,
                                  step_id=step_id, level=verdict.level_required)
            raise NotAuthorized(verdict, prompt)

    # 4. REQUESTED, before anything leaves.
    rec = _record(conn, case_id, step_id, action,
                  provider.id if provider else "internal",
                  getattr(provider, "mode", "internal"), params,
                  state="AUTHORIZED" if auth else "REQUESTED")
    chain.append(conn, case_id, "action.requested", actor,
                 {"action": action, "provider": rec["provider"],
                  "provider_mode": rec["provider_mode"],
                  "decision": verdict.rule, "because": verdict.explain,
                  "authorization_id": (auth or {}).get("id"),
                  "params": _redact(params)},
                 seal=True, subject=case.get("subject"),
                 workspace=case.get("workspace", "default"))

    _advance(conn, rec["id"], state="STARTED", started_at=ids.now())
    rec["state"] = "STARTED"

    # 5. the call.
    if provider is None:
        result = providers.ProviderResult(True, "done", "internal", "internal",
                                          message=f"{action} completed inside Agent X.")
    else:
        try:
            result = provider.bind(conn).execute(action, params)
        except providers.ProviderError as e:
            _advance(conn, rec["id"], state="FAILED", error=str(e)[:400],
                     finished_at=ids.now())
            chain.append(conn, case_id, "action.failed", "SYSTEM",
                         {"action": action, "reason": str(e)[:300]})
            return {**rec, "state": "FAILED", "error": str(e)[:400]}
        except Exception as e:                              # provider blew up
            msg = f"{type(e).__name__}: {e}"
            _advance(conn, rec["id"], state="FAILED", error=msg[:400],
                     finished_at=ids.now())
            chain.append(conn, case_id, "action.failed", "SYSTEM",
                         {"action": action, "reason": msg[:300]})
            return {**rec, "state": "FAILED", "error": msg[:400]}

    # 6. capture what came back, as evidence with its own hash.
    evidence_id = None
    if result.evidence_text:
        ev = egraph.add_evidence(
            conn, case_id=case_id, workspace=case.get("workspace", "default"),
            subject=case.get("subject") or f"case:{case_id}",
            kind=result.evidence_kind or "provider_record",
            text=result.evidence_text,
            filename=f"{result.provider}-{action}-{ids.monotonic_suffix()}.txt",
            media_type="text/plain", trust="third_party")
        evidence_id = ev["id"]

    # 7. outcome. `COMPLETED` never means `verified`.
    state = "COMPLETED" if result.ok else "FAILED"
    if result.outcome == "refused":
        state = "COMPLETED"          # the call worked; the answer was no
    _advance(conn, rec["id"], state=state, result=store.jdump(result.as_dict()),
             external_ref=result.external_ref, evidence_id=evidence_id,
             error=None if result.ok else result.message[:400],
             finished_at=ids.now())

    chain.append(conn, case_id, "action.completed", "EXTERNAL",
                 {"action": action, "provider": result.provider,
                  "provider_mode": result.mode, "outcome": result.outcome,
                  "external_ref": result.external_ref,
                  "message": result.message,
                  "evidence_id": evidence_id,
                  "verified": "unverified",
                  "responds_in_days": result.responds_in_days},
                 seal=True, subject=case.get("subject"),
                 workspace=case.get("workspace", "default"))

    return {**rec, "state": state, "outcome": result.outcome,
            "external_ref": result.external_ref, "evidence_id": evidence_id,
            "message": result.message, "data": result.data,
            "responds_in_days": result.responds_in_days,
            "provider_mode": result.mode, "verified": "unverified"}


def _record(conn, case_id: str, step_id: str | None, action: str, provider: str,
            mode: str, params: dict, *, state: str, error: str | None = None) -> dict:
    row = {"id": ids.new("ex"), "case_id": case_id, "step_id": step_id,
           "action": action, "provider": provider, "provider_mode": mode or "internal",
           "request": store.jdump(_redact(params)), "state": state,
           "verified": "unverified", "error": error, "requested_at": ids.now()}
    if state in A.TERMINAL_EXECUTION_STATES:
        row["finished_at"] = ids.now()
    _write(conn, row)
    return row


# ─────────────────────────────────────────────────────────────────────────────
# verification — the half that makes the other half true
# ─────────────────────────────────────────────────────────────────────────────
def verify(conn, *, case: dict, execution_id: str) -> dict:
    """Re-read the external system and decide whether the action's claim holds.

    Three outcomes, and the middle one is the reason this exists:

        verified      the world matches what the action claimed
        unverified    the world does not show it YET — not a failure, a wait
        contradicted  the world actively disagrees. The user is told.

    `contradicted` is the case a boolean cannot express and the one that matters
    most: a portal that said "refunded" over a ledger with no credit in it.
    """
    cols = ["id", "action", "provider", "provider_mode", "external_ref", "state",
            "evidence_id", "result"]
    with conn.cursor() as cur:
        cur.execute("SELECT id, action, provider, provider_mode, external_ref, state,"
                    " evidence_id, result FROM executions WHERE id = %s", (execution_id,))
        row = cur.fetchone()
    if not row:
        raise ValueError(f"no such execution {execution_id}")
    ex = dict(zip(cols, row))

    provider = providers.get(ex["provider"])
    if provider is None or not provider.supports("verify"):
        _advance(conn, execution_id, verified="unverifiable")
        chain.append(conn, case["id"], "action.unverifiable", "SYSTEM",
                     {"execution_id": execution_id, "action": ex["action"],
                      "provider": ex["provider"],
                      "reason": "this provider cannot be re-read, so the outcome "
                                "rests on the response alone"})
        return {"execution_id": execution_id, "verified": "unverifiable",
                "detail": "the provider offers no way to re-read the outcome"}

    result = provider.bind(conn).execute("verify", {"external_ref": ex["external_ref"],
                                                    "case_id": case["id"]})
    claimed = store.jload(ex["result"], {}) or {}
    claimed_outcome = claimed.get("outcome")

    if result.outcome == "done" and result.data.get("verified"):
        verdict = "verified"
    elif claimed_outcome == "accepted" and result.data.get("verified") is False:
        verdict = "contradicted"
    else:
        verdict = "unverified"

    evidence_id = None
    if result.evidence_text:
        ev = egraph.add_evidence(
            conn, case_id=case["id"], workspace=case.get("workspace", "default"),
            subject=case.get("subject") or f"case:{case['id']}",
            kind=result.evidence_kind or "provider_record",
            text=result.evidence_text,
            filename=f"verify-{ex['action']}-{ids.monotonic_suffix()}.txt",
            media_type="text/plain", trust="third_party")
        evidence_id = ev["id"]

    # Merge the re-read's figures into the stored result. `verified` alone is not
    # enough: `receipt._recovered()` sums `posted_minor` off `executions.result`,
    # and that column still holds the PRE-verification payload, which for a
    # straightforwardly-approved ticket carries no posted amount at all. Without
    # this the signed receipt reported no money recovered for a refund it had
    # just confirmed in the counterparty's own ledger.
    merged = dict(claimed)
    merged_data = dict(merged.get("data") or {})
    merged_data.update({k: v for k, v in result.data.items() if v is not None})
    merged["data"] = merged_data
    merged["verified_outcome"] = result.outcome
    _advance(conn, execution_id, verified=verdict, result=store.jdump(merged))
    chain.append(conn, case["id"], "action.verified", "EXTERNAL",
                 {"execution_id": execution_id, "action": ex["action"],
                  "provider": result.provider, "provider_mode": result.mode,
                  "verified": verdict, "message": result.message,
                  "evidence_id": evidence_id,
                  "posted_minor": result.data.get("posted_minor"),
                  "claimed_minor": result.data.get("claimed_minor")},
                 seal=True, subject=case.get("subject"),
                 workspace=case.get("workspace", "default"))

    return {"execution_id": execution_id, "verified": verdict,
            "detail": result.message, "evidence_id": evidence_id,
            "data": result.data,
            "amount_posted": normalize.fmt_money(result.data.get("posted_minor"),
                                                 result.data.get("currency"))}


def history(conn, case_id: str) -> list[dict]:
    cols = ["id", "step_id", "action", "provider", "provider_mode", "state",
            "outcome_json", "external_ref", "evidence_id", "verified", "error",
            "requested_at", "started_at", "finished_at"]
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, step_id, action, provider, provider_mode, state, result,"
            " external_ref, evidence_id, verified, error, requested_at, started_at,"
            " finished_at FROM executions WHERE case_id = %s ORDER BY requested_at ASC",
            (case_id,))
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        parsed = store.jload(r.pop("outcome_json"), {}) or {}
        r["outcome"] = parsed.get("outcome")
        r["message"] = parsed.get("message")
        r["data"] = parsed.get("data", {})
    return rows


def record_communication(conn, case_id: str, *, direction: str, channel: str,
                         counterparty: str | None, subject: str | None,
                         body: str, external_ref: str | None = None,
                         execution_id: str | None = None) -> dict:
    """Store an outbound or inbound message, hashed.

    Hashed because a dispute about what was said is common, and a body with a
    digest in the case chain can be shown to be the one that was sent.
    """
    import hashlib
    cid = ids.new("cm")
    sha = hashlib.sha256((body or "").encode("utf-8")).hexdigest()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO communications (id, case_id, direction, channel, counterparty,"
            " subject, body, external_ref, execution_id, sha256, sent_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (cid, case_id, direction, channel, counterparty, subject, body,
             external_ref, execution_id, sha, ids.now()))
    chain.append(conn, case_id, f"communication.{direction}", "AGENT",
                 {"channel": channel, "counterparty": counterparty,
                  "subject": subject, "sha256": sha, "external_ref": external_ref})
    return {"id": cid, "sha256": sha, "direction": direction, "channel": channel}


def communications(conn, case_id: str) -> list[dict]:
    cols = ["id", "direction", "channel", "counterparty", "subject", "body",
            "external_ref", "sha256", "sent_at"]
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, direction, channel, counterparty, subject, body, external_ref,"
            " sha256, sent_at FROM communications WHERE case_id = %s ORDER BY sent_at ASC",
            (case_id,))
        return [dict(zip(cols, r)) for r in cur.fetchall()]
