"""
The Resolution Receipt — an agent that can prove what it did.

Agent X's original claim was about deletion: not only that data was gone, but that
anyone could check. The same machinery answers a question consumers ask far more
often and get worse answers to:

    What did you actually do on my behalf, and how do I know?

A resolution receipt is the answer, and it is two documents in one artefact. The
top half is readable by a person with no context — problem, evidence, finding,
action, reference, result, verification. The bottom half is a hash-linked,
ECDSA-signed attestation binding that summary to a chain nobody can rewrite. The
same receipt satisfies the user, their bank, and an auditor.

WHAT IT ATTESTS, PRECISELY

  * the CHAIN HEAD and LENGTH of the case's own tamper-evident record. Truncating
    the chain changes the length; editing any row changes the head; both break the
    receipt.
  * the CONTENT DIGEST over the whole chain, not just its head, so an edit in the
    middle is caught even when the last row is untouched.
  * every EXECUTION with its provider, its MODE, its external reference and its
    verification state. A sandbox action says sandbox, on the receipt, in the
    user's copy.
  * the EVIDENCE digests, so the holder can re-hash their own documents.
  * the AUTONOMY the user granted and the exact words of every approval.

WHAT IT DOES NOT CLAIM

It does not claim an outcome that was not verified. `verification` carries one of
`confirmed`, `unverified`, `contradicted`, or `not applicable`, taken from the
execution records rather than from the summary text — a receipt whose headline and
whose evidence could disagree would be worse than no receipt.

And the signature proves issuance, not truth: the public key travels inside the
envelope, so a receipt on its own cannot rule out a forgery. Two things close that,
and both are supported — pin the key against the published one, or check the
attested chain head against the live case. `verify()` reports them separately so a
reader knows which assurance they actually have.
"""
from __future__ import annotations

from core.trust import certificate
from agentx import chain, ids, normalize, store
from agentx import case as case_mod
from agentx import eligibility
from agentx.evidence import contradiction, graph as egraph
from agentx.execution import runner

SPEC = "agentx-resolution-receipt/v1"


def _signing_key():
    """The product's single signing identity.

    One key across erasure certificates, compliance certificates and resolution
    receipts, because a verifier who trusts Agent X's key for one should not need a
    second key for another. `agentx.sealing` owns where it comes from and reports
    which source is in force.
    """
    from agentx import sealing
    return sealing.signing_key()


def _verification_state(executions: list[dict]) -> tuple[str, str]:
    """The honest verification verdict, taken from records rather than prose."""
    external = [e for e in executions if e.get("external_ref")]
    if not external:
        return "not applicable", "Agent X did not take an external action on this case."
    if any(e.get("verified") == "contradicted" for e in external):
        return "contradicted", ("The counterparty's own records do not match what it "
                                "told Agent X. This is unresolved.")
    if any(e.get("verified") == "verified" for e in external):
        return "confirmed", ("Re-read from the counterparty's records after the action, "
                             "not taken from its reply.")
    if any(e.get("verified") == "unverifiable" for e in external):
        return "unverifiable", ("This provider offers no way to re-read the outcome, so "
                                "the result rests on its reply alone.")
    return "unverified", ("The action was sent and acknowledged; the outcome has not yet "
                          "been confirmed against their records.")


def build(conn, case_id: str) -> dict:
    """Assemble the receipt body. Unsigned — `issue()` signs and stores it."""
    c = case_mod.get(conn, case_id)
    if not c:
        raise ValueError(f"no such case {case_id}")

    executions = runner.history(conn, case_id)
    evidence = egraph.list_evidence(conn, case_id)
    policies = eligibility.load_policies(conn, case_id)
    remedies = eligibility.load(conn, case_id)
    ch = chain.verify(conn, case_id)
    contras = contradiction.open_contradictions(conn, case_id)

    with conn.cursor() as cur:
        cur.execute("SELECT prompt, granted, granted_by, decided_at, action, level"
                    " FROM authorizations WHERE case_id = %s AND granted IS NOT NULL"
                    " ORDER BY requested_at ASC", (case_id,))
        approvals = [{"prompt": r[0], "granted": bool(r[1]), "by": r[2],
                      "at": r[3], "action": r[4], "level": r[5]}
                     for r in cur.fetchall()]

    verification, verification_note = _verification_state(executions)
    money = _recovered(executions, c)

    finding = _finding(conn, case_id, c, remedies)
    acted = [e for e in executions if e.get("external_ref")]
    headline_action = acted[-1] if acted else None

    return {
        "spec": SPEC,
        "issued_at": ids.now(),
        "case_id": case_id,
        "readable": {
            "title": "Agent X RESOLUTION RECEIPT",
            "case": case_id,
            "problem": c["title"] or (c["description"] or "")[:160],
            "problem_type": c["problem_type"],
            "opened": c["created_at"],
            "evidence": f"{len(evidence)} item(s)",
            "finding": finding,
            "action": (f"{headline_action['action'].replace('_', ' ')} via "
                       f"{headline_action['provider']}" if headline_action
                       else "no external action was taken"),
            "external_reference": (headline_action or {}).get("external_ref"),
            "result": c.get("outcome_summary") or _result_line(c, money),
            "amount_recovered": normalize.fmt_money(money, c.get("currency")) if money else None,
            "verification": verification,
            "verification_note": verification_note,
            "integrity": ("cryptographically verifiable" if ch.get("ok")
                          else "CHAIN BROKEN — do not rely on this receipt"),
            "state": c["state"],
            "engine": store.describe()["engine"],
        },
        "case": {
            "id": c["id"], "state": c["state"], "resolution": c["resolution"],
            "domain": c["domain"], "problem_type": c["problem_type"],
            "confidence": c["confidence"], "opened_at": c["created_at"],
            "closed_at": c["closed_at"],
            "amount_minor": c["amount_minor"], "currency": c["currency"],
            "autonomy_level_granted": c["autonomy_level"],
        },
        "engine": store.describe(),
        "evidence": [{"id": e["id"], "kind": e["kind"], "sha256": e["sha256"],
                      "bytes": e["bytes"], "trust": e["trust"],
                      "captured_at": e["captured_at"]} for e in evidence],
        "policies_considered": [
            {"id": p["policy_id"], "title": p["title"], "applies": p["applies"],
             "because": p["because"], "citation": p["citation"]} for p in policies],
        "remedies_considered": [
            {"kind": r["kind"], "eligibility": r["eligibility"], "rank": r["rank"],
             "because": r["because"]} for r in remedies],
        "authorizations": approvals,
        "executions": [
            {"id": e["id"], "action": e["action"], "provider": e["provider"],
             "mode": e["provider_mode"], "state": e["state"], "outcome": e.get("outcome"),
             "external_ref": e["external_ref"], "verified": e["verified"],
             "requested_at": e["requested_at"], "finished_at": e["finished_at"],
             "error": e["error"]} for e in executions],
        "open_contradictions": [
            {"predicate": x["predicate"], "severity": x["severity"],
             "detail": x["detail"]} for x in contras],
        "chain": {
            "head": ch.get("head"), "length": ch.get("rows"),
            "intact_at_issue": ch.get("ok"), "reason": ch.get("reason"),
            "content_digest": chain.digest(conn, case_id),
        },
        "how_to_verify": [
            "Recompute sha256 over the canonical JSON of `receipt` (sorted keys, "
            "separators ',' and ':', UTF-8, no whitespace) and compare it to `sha256`.",
            "Verify `signature` — ECDSA P-256 over those same bytes — against "
            "`public_key`. Pin that key against Agent X's published key: a receipt is "
            "signed by a key it carries, so key pinning is what rules out a forgery.",
            "Ask Agent X for this case's live chain head and compare it to `chain.head` "
            "and `chain.length`. A forged receipt cannot reproduce them.",
            "Re-hash any document you hold and compare it to the `sha256` in "
            "`evidence`. That proves the receipt refers to your document.",
        ],
        "honest_limits": [
            "A `mode` of `sandbox` on an execution means the counterparty was a "
            "simulated system, not a real company. It is never presented otherwise.",
            "`verification` is taken from re-reads of the counterparty's records, not "
            "from its replies. `unverified` means exactly that.",
            "Policy analysis is an engineering artefact, not legal advice.",
        ],
    }


def _finding(conn, case_id: str, c: dict, remedies: list[dict]) -> str:
    """The one-line finding, traced to claims rather than composed freely."""
    top = eligibility.best(remedies)
    money = normalize.fmt_money(c.get("amount_minor"), c.get("currency"))
    if c.get("problem_type") == "duplicate_charge" and c.get("amount_minor"):
        claim = egraph.build_claim(conn, case_id, "charge.amount",
                                   f"You were charged {money} more than once.")
        if claim:
            return (f"{claim.text} Confidence {claim.confidence:.2f}, from "
                    f"{len(claim.evidence_ids)} source(s).")
    if top:
        return f"{top['title']} established: {top['because']}"
    return "No remedy was established on the evidence available."


def _recovered(executions: list[dict], c: dict) -> int | None:
    """Money actually confirmed back, not money asked for.

    Reads only VERIFIED executions. A receipt that totalled what was requested
    would be a wish list with a signature on it.
    """
    total = 0
    for e in executions:
        if e.get("verified") != "verified":
            continue
        data = e.get("data") or {}
        amt = data.get("posted_minor") or data.get("amount_approved_minor")
        if amt:
            total += int(amt)
    return total or None


def _result_line(c: dict, money: int | None) -> str:
    if money:
        return f"{normalize.fmt_money(money, c.get('currency'))} confirmed back."
    if c["state"] == "RESOLVED":
        return "Resolved."
    if c["state"] in ("WAITING_EXTERNAL", "ACTION_SUBMITTED"):
        return "Submitted; awaiting the counterparty."
    if c["state"] == "ESCALATED":
        return "Escalated after no result at the first level."
    if c["state"] == "CLOSED_UNRESOLVED":
        return "Closed without a result; every available route was tried."
    return f"In progress ({case_mod.state_copy(c['state'])['label']})."


# ─────────────────────────────────────────────────────────────────────────────
# issue / verify
# ─────────────────────────────────────────────────────────────────────────────
def issue(conn, case_id: str, *, store_it: bool = True) -> dict:
    """Build, sign and (by default) persist the receipt.

    Persisted verbatim so that re-serving it returns byte-identical content. A
    rebuild would carry a new `issued_at`, hash differently, and break every copy
    a user had already saved — which for a document whose value is its hash is the
    same as revoking it.
    """
    body = build(conn, case_id)
    env = certificate.sign(body, _signing_key())
    env["receipt"] = env.pop("certificate")
    env["spec"] = SPEC

    if store_it:
        c = chain.verify(conn, case_id)
        rid = ids.new("rcpt")
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO receipts (id, case_id, envelope, sha256, chain_head,"
                " chain_length, signed, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (rid, case_id, store.jdump(env), env.get("sha256", ""),
                 c.get("head") or "", int(c.get("rows") or 0),
                 bool(env.get("signed")), ids.now()))
        env["receipt_id"] = rid
        chain.append(conn, case_id, "receipt.issued", "SYSTEM",
                     {"receipt_id": rid, "sha256": env.get("sha256"),
                      "signed": bool(env.get("signed")),
                      "chain_head": c.get("head"), "chain_length": c.get("rows")})
    return env


def latest(conn, case_id: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("SELECT envelope FROM receipts WHERE case_id = %s"
                    " ORDER BY created_at DESC LIMIT 1", (case_id,))
        row = cur.fetchone()
    return store.jload(row[0], None) if row else None


def verify(env: dict, conn=None, trusted_public_key: str | None = None) -> dict:
    """Check a receipt. Hash and signature need nothing but the receipt itself."""
    body = env.get("receipt")
    if not isinstance(body, dict):
        return {"ok": False, "error": "envelope has no `receipt` object", "checks": {}}

    out = certificate.verify({**env, "certificate": body}, conn=None,
                             trusted_public_key=trusted_public_key)

    if conn is not None:
        attested = body.get("chain", {})
        length = attested.get("length")
        # The attested head is the row at seq = length - 1 (0-indexed). Checking
        # that it is still THERE — rather than that it is still the chain's TIP —
        # is what lets the receipt's own storage (which appends one more row to
        # the case it just described) not invalidate the receipt it produced.
        seq = (length - 1) if isinstance(length, int) and length > 0 else None
        out["checks"]["case_chain"] = chain.verify_inclusion(
            conn, body.get("case_id", ""), seq=seq, content_hash=attested.get("head"))
    else:
        out["checks"]["case_chain"] = {"ok": None,
                                       "detail": "not checked (offline verification)"}

    decided = [c["ok"] for c in out["checks"].values() if c["ok"] is not None]
    out["ok"] = bool(decided) and all(decided)
    out["case_id"] = body.get("case_id")
    out["verification_of_outcome"] = body.get("readable", {}).get("verification")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# rendering
# ─────────────────────────────────────────────────────────────────────────────
def render_text(env: dict) -> str:
    """The receipt as a person reads it. Deliberately plain.

    A receipt someone can paste into an email to a bank is worth more than one
    that only renders inside our UI.
    """
    r = (env or {}).get("receipt") or {}
    v = r.get("readable") or {}
    lines = [
        v.get("title", "Agent X RESOLUTION RECEIPT"),
        "=" * 46,
        "",
        f"Case               {v.get('case')}",
        f"Problem            {v.get('problem')}",
        f"Opened             {v.get('opened')}",
        f"Evidence           {v.get('evidence')}",
        "",
        f"Finding            {v.get('finding')}",
        f"Action             {v.get('action')}",
        f"External reference {v.get('external_reference') or '—'}",
        f"Result             {v.get('result')}",
    ]
    if v.get("amount_recovered"):
        lines.append(f"Amount recovered   {v['amount_recovered']}")
    lines += [
        "",
        f"Verification       {v.get('verification')}",
        f"                   {v.get('verification_note')}",
        f"Integrity          {v.get('integrity')}",
        "",
        f"Chain head         {(r.get('chain') or {}).get('head', '')[:32]}…",
        f"Chain length       {(r.get('chain') or {}).get('length')}",
        f"Receipt sha256     {env.get('sha256', '')[:32]}…",
        f"Signature          {'ECDSA P-256' if env.get('signed') else 'unsigned'}",
        f"Engine             {v.get('engine')}",
        "",
        "Verify it yourself:",
    ]
    lines += [f"  · {s}" for s in (r.get("how_to_verify") or [])]
    if r.get("open_contradictions"):
        lines += ["", "Unresolved contradictions:"]
        lines += [f"  · {x['detail']}" for x in r["open_contradictions"]]
    lines += ["", "Limits:"]
    lines += [f"  · {s}" for s in (r.get("honest_limits") or [])]
    return "\n".join(lines)
