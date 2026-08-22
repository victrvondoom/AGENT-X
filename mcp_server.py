"""
Agent X MCP server.

Lets any MCP-compatible AI agent (Claude Code, Claude Desktop, Cursor, …) use Agent X's
verifiable memory as tools: remember facts, recall them (grounded + honest), provably forget a
subject with proof, and inspect the memory. Backed by CockroachDB — the same engine the web app uses.

It also exposes the consumer resolution engine as tools, below the memory tools: open a case
from a plain-English description, attach evidence, answer the question the case is stuck on,
advance its plan, approve or decline an action, and pull its signed receipt — the same
governed pipeline `/agentx` drives, reachable from inside a conversation with no browser at
all. A calling agent can therefore run an entire consumer resolution end to end, including the
autonomy gate: `advance_case` reports a pending approval rather than silently acting past it,
because an MCP client is a caller like any other and gets no special exemption from consent.

Run:      python mcp_server.py
Connect:  claude mcp add agent-x -- python /ABS/PATH/agent-x/mcp_server.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastmcp import FastMCP  # noqa: E402

from core.ingest import ingest_document          # noqa: E402
from core.ask import ask as _ask                  # noqa: E402
from core.forget import (forget as _forget, prior_state, verify_gone,   # noqa: E402
                         LegalHold, set_hold as _set_hold, release_hold as _release_hold)
from db import store                              # noqa: E402

mcp = FastMCP("agent-x")


@mcp.tool
def remember(subject: str, text: str, workspace: str = "default",
             source_kind: str = "user_evidence") -> dict:
    """Store a memory about `subject` (a person, system, or entity) in the given workspace.
    Extracts entities and relationships into the knowledge graph. Returns a small receipt.

    source_kind decides whether a later erasure may touch it: "user_evidence" (a person's
    records, erased with them), "authoritative" (a statute or charter — no personal data, so
    it survives any subject's erasure), or "derived".
    """
    try:
        return ingest_document(subject, subject, text, workspace, source_kind)
    except store.KeyDestroyed:
        return {"error": f"'{subject}' was permanently erased; the name cannot be re-onboarded"}


@mcp.tool
def recall(query: str, workspace: str = "default") -> str:
    """Answer a question strictly from stored memory. If the answer isn't on record, says so —
    it never hallucinates. This honesty is what makes Agent X's forgetting provable."""
    return _ask(query, None, workspace)[0]


@mcp.tool
def forget(subject: str, workspace: str = "default") -> dict:
    """Verifiably erase `subject` from memory: one atomic CockroachDB transaction deletes its
    documents, graph nodes, edges, and vectors, and crypto-shreds its key. Returns a 3-part proof
    (it existed via AS OF SYSTEM TIME · it's gone · it's irreversible).

    May REFUSE. If the subject is under legal hold this returns {"erased": false} with the
    reason instead of raising — a calling agent needs to read the refusal and explain it,
    not crash on it. Authoritative sources (statutes, regulator charters) are never erased."""
    try:
        r = _forget(subject, workspace)
    except LegalHold as e:
        return {
            "erased": False,
            "refused": "legal_hold",
            "subject": e.subject, "reason": e.reason, "until": e.until,
            "basis": "GDPR Art. 17(3) — erasure does not apply where retention is legally required",
        }
    return {
        "erased": True,
        "receipt": r,
        "proof_prior_existence": prior_state(subject, r["t_before"], workspace),
        "proof_of_absence": verify_gone(subject, workspace),
    }


@mcp.tool
def legal_hold(subject: str, reason: str, until: str = "", workspace: str = "default") -> dict:
    """Place `subject` under legal hold so erasure is REFUSED while a retention obligation
    applies (litigation, statutory record-keeping). `until` is an optional ISO timestamp; a
    hold with no end date holds indefinitely until released."""
    return _set_hold(subject, reason, until or None, workspace)


@mcp.tool
def release_legal_hold(subject: str, workspace: str = "default") -> dict:
    """Lift a legal hold, permitting erasure again. The release is written to the timeline."""
    return _release_hold(subject, workspace)


@mcp.tool
def list_subjects(workspace: str = "default") -> list:
    """List the subjects currently held in memory for a workspace."""
    with store.connect() as conn, conn.cursor() as c:
        c.execute("SELECT DISTINCT subject FROM documents WHERE workspace = %s ORDER BY subject",
                  (workspace,))
        return [r[0] for r in c.fetchall()]


@mcp.tool
def memory_timeline(workspace: str = "default") -> list:
    """Recent memory events (ingest / forget / demote / restore) for a workspace, newest first."""
    with store.connect() as conn, conn.cursor() as c:
        c.execute(
            "SELECT kind, subject, detail, created_at::string FROM timeline "
            "WHERE workspace = %s ORDER BY created_at DESC LIMIT 50",
            (workspace,),
        )
        return [{"kind": r[0], "subject": r[1], "detail": r[2], "at": r[3]} for r in c.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# consumer resolution engine — same pipeline /agentx drives, reachable as tools
# ─────────────────────────────────────────────────────────────────────────────
from agentx import engine as _engine                          # noqa: E402
from agentx import case as _case_mod                          # noqa: E402
from agentx import eligibility as _eligibility                # noqa: E402
from agentx import receipt as _receipt                        # noqa: E402
from agentx import store as _agentx_store                     # noqa: E402
from agentx.execution import providers as _agentx_providers   # noqa: E402
from agentx.execution import runner as _runner                # noqa: E402


def _agentx_ready() -> None:
    _agentx_store.ensure_schema()
    _agentx_providers.bootstrap()


def _condensed(snap: dict) -> dict:
    """The parts of a case snapshot worth putting in front of a calling agent.

    `engine.snapshot()` returns everything — every fact, every chain row, every
    execution's full request/response payload — which is right for a UI that
    lets a person drill down, and wrong for an MCP tool result an LLM has to read
    in full every time. This keeps what a caller needs to decide its NEXT move:
    is anything waiting on me, what does Agent X believe, what would it do next.
    """
    c = snap["case"]
    return {
        "case_id": c["id"], "state": c["state"],
        "state_label": (c.get("state_copy") or {}).get("label"),
        "state_detail": (c.get("state_copy") or {}).get("detail"),
        "problem_type": c["problem_type"], "domain": c["domain"],
        "confidence": c["confidence"], "amount": c["amount"],
        "resolution": c["resolution"], "outcome_summary": c["outcome_summary"],
        "headline": snap["headline"],
        "top_claims": [cl["claim"] for cl in (snap.get("claims") or [])[:3]],
        "open_questions": [{"id": q["id"], "question": q["question"], "why": q["why"],
                            "options": q.get("options") or []}
                           for q in (snap.get("questions") or [])],
        "pending_approvals": [{"id": a["id"], "action": a["action"], "prompt": a["prompt"]}
                              for a in (snap.get("approvals") or [])],
        "top_remedies": [{"kind": r["kind"], "eligibility": r["eligibility"],
                          "because": r["because"]}
                         for r in (snap.get("remedies") or [])[:3]],
        "open_contradictions": [x["detail"] for x in (snap.get("contradictions") or [])],
        "chain_rows": (snap.get("chain") or {}).get("length"),
        "chain_head": (snap.get("chain") or {}).get("head"),
        "engine": (snap.get("engine") or {}).get("engine"),
    }


@mcp.tool
def open_case(description: str, user_ref: str = "mcp-user", workspace: str = "default",
             autonomy_level: int = 2, use_llm: bool = True) -> dict:
    """Open a consumer resolution case from a plain-English description of what
    happened ("I was charged twice", "my flight was delayed"). Runs the same
    pipeline as the /agentx web workspace: classification, evidence intake,
    policy analysis and planning, as far as the current evidence allows.

    autonomy_level (0-4) is the ceiling on what Agent X may do unattended for
    this case — 0 is read-only, 2 ("prepare and confirm", the default) drafts
    and stages every outbound action for approval, 4 allows irreversible actions
    within limits but never a high-risk one without explicit consent regardless
    of level. Returns a condensed case summary; call `case_status` any time for
    the current one, or `advance_case` to push a validated plan forward."""
    _agentx_ready()
    with _agentx_store.connect() as conn:
        snap = _engine.intake(conn, description=description, user_ref=user_ref,
                              workspace=workspace, autonomy_level=autonomy_level,
                              use_llm=use_llm)
        return _condensed(snap)


@mcp.tool
def case_status(case_id: str) -> dict:
    """The current state of a case: what Agent X believes, what it would do
    next, and — most importantly for a calling agent — anything waiting on a
    decision (an open question or a pending action approval)."""
    _agentx_ready()
    with _agentx_store.connect() as conn:
        try:
            return _condensed(_engine.snapshot(conn, case_id))
        except ValueError as e:
            return {"error": str(e)}


@mcp.tool
def list_cases(workspace: str = "default", state: str = "") -> list:
    """List cases in a workspace. `state` filters to "open" (not yet resolved),
    "closed" (resolved/closed/withdrawn), a specific state name, or "" for all."""
    _agentx_ready()
    with _agentx_store.connect() as conn:
        rows = _case_mod.list_cases(conn, workspace=workspace, state=state or None)
        out = []
        for c in rows:
            remedies = _eligibility.load(conn, c["id"])
            out.append({
                "case_id": c["id"], "state": c["state"], "title": c["title"],
                "problem_type": c["problem_type"],
                "headline": _eligibility.headline(remedies, c["amount_minor"], c["currency"]),
            })
        return out


@mcp.tool
def attach_evidence(case_id: str, kind: str, text: str, filename: str = "",
                    use_llm: bool = True) -> dict:
    """Attach one piece of evidence to a case and re-run analysis. `kind` is one
    of Agent X's evidence types: transaction, bank_statement, receipt, invoice,
    order_confirmation, booking_confirmation, cancellation_notice, email, and
    others — call `list_evidence_kinds` for the full set with descriptions.
    `text` is the document's text content. Every fact extracted from it is
    traced back to the line it came from, and if it disagrees with something
    already on the case, that is flagged as a contradiction rather than
    resolved silently."""
    _agentx_ready()
    with _agentx_store.connect() as conn:
        try:
            out = _engine.attach(conn, case_id, kind=kind, text=text,
                                 filename=filename or None, use_llm=use_llm)
        except ValueError as e:
            return {"error": str(e)}
        result = {"facts_extracted": len(out["facts"]),
                  "contradictions_found": len(out["contradictions"])}
        if out.get("case"):
            result["case"] = _condensed(out["case"])
        return result


@mcp.tool
def list_evidence_kinds() -> dict:
    """The evidence types `attach_evidence` accepts, with what each one is."""
    from agentx.ontology import EVIDENCE_KINDS
    return {k: v["label"] for k, v in EVIDENCE_KINDS.items()}


@mcp.tool
def answer_case_question(case_id: str, question_id: str, value: str,
                         use_llm: bool = True) -> dict:
    """Answer one of a case's open questions (from `case_status`'s
    `open_questions`) and re-run analysis with the answer folded in. Free text
    is accepted even for choice-type questions; pass the option text as shown."""
    _agentx_ready()
    with _agentx_store.connect() as conn:
        try:
            return _condensed(_engine.answer_question(conn, case_id, question_id,
                                                       value, use_llm=use_llm))
        except ValueError as e:
            return {"error": str(e)}


@mcp.tool
def advance_case(case_id: str, max_steps: int = 6) -> dict:
    """Push a case's validated plan forward as far as its granted autonomy
    allows. Stops the moment a step needs authorisation the case does not have
    — reported back as a pending approval in the result, never acted on
    silently. Call `approve_case_action` to grant one, then call this again."""
    _agentx_ready()
    with _agentx_store.connect() as conn:
        try:
            out = _engine.advance(conn, case_id, max_steps=max_steps)
        except ValueError as e:
            return {"error": str(e)}
        ran = [{"action": r.get("action"),
               "outcome": r.get("outcome") or r.get("blocked") or r.get("state"),
               "message": r.get("message")} for r in out.get("ran", [])]
        result = {"steps_taken": ran}
        if out.get("case"):
            result["case"] = _condensed(out["case"])
        return result


@mcp.tool
def approve_case_action(case_id: str, authorization_id: str, granted: bool = True,
                        by: str = "mcp-user") -> dict:
    """Grant or decline a pending action approval (its id comes from
    `case_status`'s `pending_approvals`, each carrying the exact sentence the
    action was described with). Granting immediately continues the plan;
    declining returns the case to investigation rather than retrying the same
    action."""
    _agentx_ready()
    with _agentx_store.connect() as conn:
        try:
            return _condensed(_engine.approve(conn, case_id, authorization_id,
                                              granted=granted, by=by))
        except ValueError as e:
            return {"error": str(e)}


@mcp.tool
def what_worked_before(counterparty: str = "", problem_type: str = "",
                       workspace: str = "default") -> dict:
    """What Agent X learned from cases it already closed against this company and
    problem type: which remedy paid out, how many chases it took, whether
    escalation was needed, and — where the evidence is strong enough — whether
    the company refuses this class of claim as a matter of course.

    Structural records only, with no personal data in them, so this stays true
    and available even after the cases it was learned from are erased."""
    _agentx_ready()
    from agentx import outcomes as _outcomes
    with _agentx_store.connect() as conn:
        out = {"prior": _outcomes.prior_for(conn, workspace=workspace,
                                            counterparty=counterparty or None,
                                            problem_type=problem_type or None)}
        if counterparty and problem_type:
            out["systemic"] = _outcomes.systemic_signal(
                conn, workspace=workspace, counterparty=counterparty,
                problem_type=problem_type)
        return out


@mcp.tool
def case_receipt(case_id: str) -> dict:
    """The case's signed Resolution Receipt: problem, finding, evidence, action
    taken, external reference, result, and — critically — whether the outcome
    was independently VERIFIED against the counterparty's own records or is
    still unverified. Includes the plain-text rendering and the hash-chain
    binding a caller can check without trusting this server."""
    _agentx_ready()
    with _agentx_store.connect() as conn:
        try:
            env = _receipt.latest(conn, case_id) or _receipt.issue(conn, case_id)
        except ValueError as e:
            return {"error": str(e)}
        v = _receipt.verify(env, conn=conn)
        return {"text": _receipt.render_text(env), "sha256": env.get("sha256"),
                "signed": bool(env.get("signed")), "verification_ok": v.get("ok"),
                "readable": (env.get("receipt") or {}).get("readable", {})}


@mcp.tool
def forget_case(case_id: str) -> dict:
    """Crypto-shred one case's sealed content (the erasure subject is scoped to
    just this case, not the user's whole history). The key is destroyed, the
    content becomes unrecoverable, and — the property that makes this provable
    rather than asserted — the case's hash chain still verifies afterwards,
    because the ciphertext was hashed and was never touched."""
    _agentx_ready()
    with _agentx_store.connect() as conn:
        try:
            out = _case_mod.forget(conn, case_id)
        except ValueError as e:
            return {"error": str(e)}
        return {"case_id": case_id, "unrecoverable": out["unrecoverable"],
                "chain_intact_after": out["chain_intact_after"]}


if __name__ == "__main__":
    mcp.run()
