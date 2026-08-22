"""
Agent X's HTTP surface.

One router, mounted into the same FastAPI app as the erasure and document
pipelines, because this is one product with one trust spine. The shape follows the
case: almost everything is `/api/agentx/cases/{id}/…`, and the read endpoint returns
the WHOLE case in one call rather than eight.

That last decision is deliberate. A consumer screen assembled from eight endpoints
will, sooner or later, render an approval card next to a state badge that
contradicts it — and on a screen whose whole job is asking someone to authorise a
real-world action, that is not a cosmetic bug.

WHAT IS PUBLIC AND WHAT IS NOT

Reads are open: the ontology, the capability registry, the provider list with its
modes, the governor's policy, the public key, and verification. An agent that
claims to be inspectable has to be inspectable without credentials.

Writes are token-gated by the same `AGENT_X_AUTH_TOKEN` the rest of the app uses.
Agent X executes actions against external systems on a user's behalf; leaving that
open to anonymous callers would be indefensible whatever the demo convenience.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from agentx import capabilities as caps
from agentx import case as case_mod
from agentx import chain, demo, eligibility, engine, followup, governor, ids
from agentx import ontology, outcomes, planner, policy, receipt, sealing, store, understanding
from agentx.evidence import contradiction, graph as egraph, package as pkg
from agentx.execution import actions as A
from agentx.execution import providers, runner
from agentx.sandbox import world

router = APIRouter(prefix="/api/agentx", tags=["agentx"])

# Text-bearing uploads Agent X can actually read. Anything else is stored and
# hashed, and reported as having no text layer — never silently treated as empty.
TEXT_TYPES = {"text/plain", "text/markdown", "text/csv", "message/rfc822",
              "application/json", "text/html", ""}
TEXT_SUFFIXES = (".txt", ".md", ".csv", ".eml", ".json", ".log", ".html")


def require_auth(authorization: str | None = Header(None)) -> None:
    token = os.environ.get("AGENT_X_AUTH_TOKEN")
    if not token:
        return
    if authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="a bearer token is required for "
                                                    "actions that change a case")


def _conn():
    store.ensure_schema()
    providers.bootstrap()
    return store.connect()


# ─────────────────────────────────────────────────────────────────────────────
# what this system is
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/health")
def health():
    """Engine, key sources, providers and catalogue size — the honest boot report."""
    store.ensure_schema()
    return {
        "ok": True,
        # The UI needs to know whether to ask for a token before it offers a
        # button that will 401. Whether auth is ON is not a secret; the token is.
        "auth_required": bool(os.environ.get("AGENT_X_AUTH_TOKEN")),
        "engine": store.describe(),
        "providers": providers.bootstrap(),
        "ontology": ontology.summary(),
        "autonomy_levels": governor.describe_levels(),
        "actions": [a["verb"] for a in A.catalogue()],
    }


@router.get("/ontology")
def ontology_index(problem_type: str | None = None):
    if problem_type:
        d = ontology.get(problem_type)
        if not d:
            raise HTTPException(404, f"no problem type {problem_type!r}")
        return d.as_dict()
    return {**ontology.summary(),
            "types": [{"problem_type": d.problem_type, "domain": d.domain,
                       "label": d.label, "summary": d.summary.strip(),
                       "risk": d.risk, "remedies": list(d.resolution_strategies),
                       "ambiguity_group": d.ambiguity_group}
                      for d in sorted(ontology.catalogue().values(),
                                      key=lambda x: (x.domain, x.problem_type))]}


@router.get("/policies")
def policies_index():
    return {"count": len(policy.corpus()),
            "dangling_references": policy.missing_references(),
            "policies": [{"id": p.id, "title": p.title, "authority": p.authority,
                          "jurisdiction": p.jurisdiction, "citation": p.citation,
                          "summary": p.summary, "window_days": p.window_days,
                          "grants": list(p.grants)}
                         for p in policy.corpus().values()]}


@router.get("/capabilities")
def capabilities_index():
    return caps.summary()


@router.get("/providers")
def providers_index():
    return providers.summary()


@router.get("/governor")
def governor_policy():
    return {"levels": governor.describe_levels(),
            "policy": governor.policy_snapshot(),
            "actions": A.catalogue()}


@router.get("/public-key", response_class=PlainTextResponse)
def public_key():
    """Agent X's signing key, for pinning a receipt against something we published.

    A receipt carries the key it was signed with, so it can never rule out a
    forgery on its own terms. Pinning against this is one of the two things that
    can; checking the attested chain head against the live case is the other.
    """
    return sealing.public_key_pem()


class UnderstandReq(BaseModel):
    text: str
    use_llm: bool = False


@router.post("/understand")
def api_understand(r: UnderstandReq):
    """The classifier with no case attached — the ambiguity claim, inspectable.

    Public and side-effect free on purpose: the strongest thing to hand a sceptic
    is the ability to type a sentence and watch six interpretations stay alive.
    """
    u = understanding.understand(r.text, use_llm=r.use_llm)
    return {**u.as_dict(),
            "questions": understanding.rank_discriminators(u.hypotheses, limit=3)}


# ─────────────────────────────────────────────────────────────────────────────
# cases
# ─────────────────────────────────────────────────────────────────────────────
class IntakeReq(BaseModel):
    description: str
    user_ref: str = "demo-user"
    workspace: str = "default"
    autonomy_level: int = 2
    use_llm: bool = True


@router.post("/cases")
def create_case(r: IntakeReq, _: None = Depends(require_auth)):
    if not (r.description or "").strip():
        raise HTTPException(400, "tell Agent X what happened")
    with _conn() as conn:
        return engine.intake(conn, description=r.description, user_ref=r.user_ref,
                             workspace=r.workspace,
                             autonomy_level=r.autonomy_level, use_llm=r.use_llm)


@router.get("/cases")
def list_cases(workspace: str = "default", state: str | None = None,
               user_ref: str | None = None, limit: int = 50):
    with _conn() as conn:
        rows = case_mod.list_cases(conn, workspace=workspace, user_ref=user_ref,
                                   state=state, limit=limit)
        from agentx import normalize
        out = []
        for c in rows:
            remedies = eligibility.load(conn, c["id"])
            out.append({**c,
                        "state_copy": case_mod.state_copy(c["state"]),
                        "amount": normalize.fmt_money(c["amount_minor"], c["currency"]),
                        "headline": eligibility.headline(remedies, c["amount_minor"],
                                                         c["currency"]),
                        "open_questions": len(case_mod.open_questions(conn, c["id"])),
                        "pending_approvals": len(engine.pending_approvals(conn, c["id"]))})
        return out


@router.get("/cases/{case_id}")
def get_case(case_id: str):
    with _conn() as conn:
        try:
            return engine.snapshot(conn, case_id)
        except ValueError as e:
            raise HTTPException(404, str(e))


class EvidenceReq(BaseModel):
    kind: str
    text: str
    filename: str | None = None
    use_llm: bool = True


@router.post("/cases/{case_id}/evidence")
def add_evidence(case_id: str, r: EvidenceReq, _: None = Depends(require_auth)):
    with _conn() as conn:
        try:
            return engine.attach(conn, case_id, kind=r.kind, text=r.text,
                                 filename=r.filename, use_llm=r.use_llm)
        except ValueError as e:
            raise HTTPException(400, str(e))


@router.post("/cases/{case_id}/upload")
async def upload_evidence(case_id: str, file: UploadFile = File(...),
                          kind: str = Form("screenshot"),
                          _: None = Depends(require_auth)):
    """Attach a file. Text is read; anything else is hashed and honestly labelled.

    There is no OCR here and none is pretended. A PDF or a photograph is stored,
    hashed and listed as evidence with no text layer, and the response says so —
    so a user can see that Agent X holds their document without believing it has
    read it. Wiring a real extraction service in means one provider, not a change
    to this endpoint's contract.
    """
    raw = await file.read()
    name = file.filename or "upload"
    is_text = (file.content_type in TEXT_TYPES
               or name.lower().endswith(TEXT_SUFFIXES))
    text = ""
    note = None
    if is_text:
        text = raw.decode("utf-8", errors="replace")
    else:
        note = (f"{file.content_type or 'this file type'} has no text layer Agent X "
                f"can read. It is stored and hashed as evidence, and no facts were "
                f"extracted from it.")
    with _conn() as conn:
        try:
            out = engine.attach(conn, case_id, kind=kind, text=text, raw=raw,
                                filename=name, media_type=file.content_type)
        except ValueError as e:
            raise HTTPException(400, str(e))
    if note:
        out["note"] = note
    return out


class AnswerReq(BaseModel):
    question_id: str
    value: str
    use_llm: bool = True


@router.post("/cases/{case_id}/answer")
def answer(case_id: str, r: AnswerReq, _: None = Depends(require_auth)):
    with _conn() as conn:
        try:
            return engine.answer_question(conn, case_id, r.question_id, r.value,
                                          use_llm=r.use_llm)
        except ValueError as e:
            raise HTTPException(400, str(e))


class AdvanceReq(BaseModel):
    max_steps: int = 6


@router.post("/cases/{case_id}/advance")
def advance(case_id: str, r: AdvanceReq | None = None,
            _: None = Depends(require_auth)):
    with _conn() as conn:
        return engine.advance(conn, case_id, max_steps=(r.max_steps if r else 6))


class ApproveReq(BaseModel):
    authorization_id: str
    granted: bool = True
    by: str = "user"


@router.post("/cases/{case_id}/approve")
def approve(case_id: str, r: ApproveReq, _: None = Depends(require_auth)):
    with _conn() as conn:
        try:
            return engine.approve(conn, case_id, r.authorization_id,
                                  granted=r.granted, by=r.by)
        except ValueError as e:
            raise HTTPException(400, str(e))


class AutonomyReq(BaseModel):
    level: int


@router.post("/cases/{case_id}/autonomy")
def set_autonomy(case_id: str, r: AutonomyReq, _: None = Depends(require_auth)):
    with _conn() as conn:
        case_mod.set_autonomy(conn, case_id, r.level)
        return engine.snapshot(conn, case_id)


class CloseReq(BaseModel):
    resolution: str = "withdrawn"
    summary: str = ""


@router.post("/cases/{case_id}/close")
def close_case(case_id: str, r: CloseReq, _: None = Depends(require_auth)):
    with _conn() as conn:
        return engine.close(conn, case_id, resolution=r.resolution, summary=r.summary)


# ─────────────────────────────────────────────────────────────────────────────
# evidence, contradictions, chain
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/cases/{case_id}/evidence/{evidence_id}", response_class=PlainTextResponse)
def evidence_text(case_id: str, evidence_id: str):
    """The stored text of one artefact, unsealed — or the tombstone after erasure."""
    with _conn() as conn:
        text = egraph.evidence_text(conn, evidence_id)
    if text is None:
        return ("This evidence was sealed under the case key, and that key has been "
                "destroyed. The content is unrecoverable, which is exactly what the "
                "erasure was for. The case chain still verifies.")
    return text


class ContradictionReq(BaseModel):
    resolution: str
    keep_fact: str | None = None


@router.post("/cases/{case_id}/contradictions/{contradiction_id}")
def explain_contradiction(case_id: str, contradiction_id: str, r: ContradictionReq,
                          _: None = Depends(require_auth)):
    with _conn() as conn:
        try:
            out = contradiction.explain(conn, contradiction_id, r.resolution,
                                        keep_fact=r.keep_fact)
        except ValueError as e:
            raise HTTPException(404, str(e))
        chain.append(conn, case_id, "contradiction.explained", "HUMAN",
                     {"because": r.resolution, "predicate": out["predicate"]})
        return engine.investigate(conn, case_id)


@router.get("/cases/{case_id}/chain")
def case_chain(case_id: str):
    """The case's tamper-evident record, readable, with its verification."""
    with _conn() as conn:
        return {"case_id": case_id,
                "verification": chain.verify(conn, case_id),
                "content_digest": chain.digest(conn, case_id),
                "rows": chain.readable(conn, case_id)}


# ─────────────────────────────────────────────────────────────────────────────
# receipt, package, verification, erasure
# ─────────────────────────────────────────────────────────────────────────────
@router.post("/cases/{case_id}/receipt")
def issue_receipt(case_id: str, _: None = Depends(require_auth)):
    with _conn() as conn:
        env = receipt.issue(conn, case_id)
        return {"envelope": env, "text": receipt.render_text(env)}


@router.get("/cases/{case_id}/receipt")
def get_receipt(case_id: str):
    with _conn() as conn:
        env = receipt.latest(conn, case_id) or receipt.issue(conn, case_id)
        return {"envelope": env, "text": receipt.render_text(env),
                "verification": receipt.verify(env, conn=conn)}


@router.get("/cases/{case_id}/receipt.txt", response_class=PlainTextResponse)
def get_receipt_text(case_id: str):
    with _conn() as conn:
        env = receipt.latest(conn, case_id) or receipt.issue(conn, case_id)
        return receipt.render_text(env)


@router.post("/receipt/verify")
def verify_receipt(envelope: dict, check_chain: bool = True,
                   trusted_public_key: str | None = None):
    """Verify a receipt someone pasted in. Works with no database at all.

    `check_chain=false` is the offline path: hash and signature only, exactly what
    a recipient with a saved copy and our published key can do without asking us
    anything.
    """
    if check_chain:
        with _conn() as conn:
            return receipt.verify(envelope, conn=conn,
                                  trusted_public_key=trusted_public_key)
    return receipt.verify(envelope, conn=None, trusted_public_key=trusted_public_key)


@router.get("/cases/{case_id}/package")
def evidence_package(case_id: str, audience: str = "human_review"):
    with _conn() as conn:
        try:
            return engine.evidence_package(conn, case_id, audience=audience)
        except ValueError as e:
            raise HTTPException(400, str(e))


@router.post("/package/verify")
def verify_package(envelope: dict, check_chain: bool = True):
    if check_chain:
        with _conn() as conn:
            return pkg.verify(envelope, conn=conn)
    return pkg.verify(envelope, conn=None)


@router.post("/cases/{case_id}/forget")
def forget_case(case_id: str, _: None = Depends(require_auth)):
    """Crypto-shred one case, and return the proof that the chain survived it."""
    with _conn() as conn:
        try:
            out = case_mod.forget(conn, case_id)
        except ValueError as e:
            raise HTTPException(404, str(e))
        return {**out, "chain": chain.verify(conn, case_id)}


# ─────────────────────────────────────────────────────────────────────────────
# outcome memory
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/outcomes")
def outcomes_index(workspace: str = "default", counterparty: str | None = None,
                   problem_type: str | None = None, mode: str | None = None):
    """What Agent X has learned from cases it already closed.

    Public, and deliberately so: this table contains no personal data by
    construction (see db/migrations/007_outcomes.sql), and the whole claim —
    that the learning survives erasure because it was never personal — is only
    checkable if the table is inspectable.
    """
    with _conn() as conn:
        return {
            "outcomes": outcomes.history(conn, workspace=workspace,
                                         counterparty=counterparty,
                                         problem_type=problem_type, mode=mode),
            "prior": (outcomes.prior_for(conn, workspace=workspace,
                                         counterparty=counterparty,
                                         problem_type=problem_type, mode=mode)
                      if (counterparty or problem_type) else None),
            "systemic": (outcomes.systemic_signal(conn, workspace=workspace,
                                                  counterparty=counterparty,
                                                  problem_type=problem_type)
                         if (counterparty and problem_type) else None),
            "note": ("Structural records only — company, problem class, remedy, "
                     "chases, escalation, duration, recovery ratio. No amounts, "
                     "no references, no narrative, no user."),
        }


# ─────────────────────────────────────────────────────────────────────────────
# the scheduler
# ─────────────────────────────────────────────────────────────────────────────
class SweepReq(BaseModel):
    workspace: str = "default"
    as_of: str | None = None


@router.post("/sweep")
def sweep(r: SweepReq | None = None, _: None = Depends(require_auth)):
    """Run the follow-up agent once. What a cron calls."""
    r = r or SweepReq()
    with _conn() as conn:
        return followup.sweep(conn, as_of=r.as_of, workspace=r.workspace)


@router.get("/due")
def due(as_of: str | None = None):
    with _conn() as conn:
        return {"as_of": as_of or ids.now(),
                "due": [{k: v for k, v in f.items() if k != "case"}
                        for f in followup.due(conn, as_of=as_of)]}


# ─────────────────────────────────────────────────────────────────────────────
# the demonstration
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/demo/scenarios")
def demo_scenarios():
    return [{"key": k, "title": v["title"], "one_liner": v["one_liner"],
             "narrative": v["narrative"], "expect": v["expect"],
             "evidence": [{"kind": e[0], "filename": e[1]} for e in v["evidence"]]}
            for k, v in sorted(demo.SCENARIOS.items())]


@router.get("/demo/ambiguity")
def demo_ambiguity():
    """Four words, six live readings, one question. No database involved."""
    return demo.ambiguity_probe()


class DemoRunReq(BaseModel):
    auto_approve: bool = True
    autonomy: int = 2
    use_llm: bool = False


@router.post("/demo/run/{key}")
def demo_run(key: str, r: DemoRunReq | None = None, _: None = Depends(require_auth)):
    r = r or DemoRunReq()
    with _conn() as conn:
        try:
            return demo.run(conn, key, auto_approve=r.auto_approve,
                            autonomy=r.autonomy, use_llm=r.use_llm)
        except ValueError as e:
            raise HTTPException(404, str(e))


class ClockReq(BaseModel):
    days: float = 1.0
    workspace: str = "default"


@router.post("/demo/advance-clock")
def demo_clock(r: ClockReq, _: None = Depends(require_auth)):
    """Move the sandbox world forward and run the scheduler at the new instant.

    A demo-only endpoint, and the only place in the system that touches the
    sandbox clock. Production scheduling reads the real clock and cannot be moved
    from here.
    """
    with _conn() as conn:
        moved = world.advance(conn, r.days)
        as_of = ids.in_days(world.clock_offset(conn))
        swept = followup.sweep(conn, as_of=as_of, workspace=r.workspace)
        return {"clock": moved, "as_of": as_of, "sweep": swept}


@router.post("/demo/reset")
def demo_reset(_: None = Depends(require_auth)):
    with _conn() as conn:
        out = demo.reset(conn)
        out.update(demo.seed(conn))
        return out


@router.get("/demo/world")
def demo_world(company: str | None = None):
    """The sandbox companies' own records — inspectable next to the case file.

    Public, and the point is comparison: a judge can read what SkyLink holds and
    check it against what Agent X's receipt claims, without taking either on trust.
    """
    with _conn() as conn:
        return {"companies": world.COMPANIES,
                "clock_offset_days": world.clock_offset(conn),
                "sandbox_now": world.now(conn),
                "objects": world.all_objects(conn, company)}
