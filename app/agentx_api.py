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
from agentx import chain, demo, documents, eligibility, engine, followup, governor, ids
from agentx import knowledge, sentinel, speech, tracks
from agentx import ontology, outcomes, planner, policy, receipt, sealing, store, understanding
from agentx.evidence import contradiction, graph as egraph, package as pkg
from agentx.execution import actions as A
from agentx.execution import providers, runner
from agentx.sandbox import world

router = APIRouter(prefix="/api/agentx", tags=["agentx"])

# Which uploads have a readable text layer, and how one is read, lives in
# `agentx.documents` — the engine needs the same answer when evidence arrives by
# a route other than this endpoint, and two copies of that list would drift.


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
        # What the research layer can and cannot answer from. Reported for the
        # same reason `engine` is: the corpus covers five regulatory sectors, and
        # a caller is entitled to know that before reading anything into a case
        # that retrieved nothing.
        "knowledge": knowledge.stats(),
        "voice": speech.availability(),
        "tracks": tracks.summary(),
        "self_healing": {"enabled": True, "governed": True,
                         "detail": "The sentinel scans for stalled cases. Every "
                                   "remediation goes through the governor; an "
                                   "action needing approval is reported, never "
                                   "performed."},
        "autonomy_levels": governor.describe_levels(),
        "actions": [a["verb"] for a in A.catalogue()],
    }


@router.get("/tracks")
def tracks_index(usable_only: bool = False):
    """Everything Agent X can do, and honestly which of it works right now.

    Public: a person deciding whether this product is worth their afternoon
    should not need a token to find out. `status` is resolved at call time by
    importing the code behind each track, so a track cannot claim to work after
    the module backing it has been removed.
    """
    return {**tracks.summary(), "tracks": tracks.catalogue(usable_only=usable_only)}


@router.get("/knowledge")
def knowledge_search(q: str = "", limit: int = 5):
    """Search the regulatory corpus directly, outside any case.

    Public, like `/ontology` and `/verify` — the corpus is published guidance,
    not case data. Returning an empty list is a normal answer, and the response
    says which sectors exist so "nothing found" can be told apart from "nothing
    on this subject exists".
    """
    limit = max(1, min(limit, 20))
    hits = knowledge.search(q, limit=limit) if q.strip() else []
    return {"query": q, "results": hits, "count": len(hits),
            "sectors": list(knowledge.sectors()),
            "note": ("Retrieval is deterministic BM25 over a corpus checked into "
                     "the repository. An empty result means this corpus does not "
                     "cover the subject — it is not a degraded answer.")}


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
    # How the description was given. Recorded on the chain because a dictated
    # account is a materially different artefact from a typed one — a transcript
    # can mishear an amount or a reference in ways typing does not, and someone
    # auditing a case later is entitled to know which they are reading.
    spoken: bool = False
    language: str | None = None


@router.post("/cases")
def create_case(r: IntakeReq, _: None = Depends(require_auth)):
    if not (r.description or "").strip():
        raise HTTPException(400, "tell Agent X what happened")
    with _conn() as conn:
        out = engine.intake(conn, description=r.description, user_ref=r.user_ref,
                            workspace=r.workspace,
                            autonomy_level=r.autonomy_level, use_llm=r.use_llm)
        if r.spoken:
            case_id = out["case"]["id"]
            chain.append(conn, case_id, "intake.dictated", "HUMAN",
                         {"language": r.language,
                          "because": "the description was spoken, not typed",
                          "audio_retained": False})
            out["language_note"] = speech.language_note(r.description, r.language)
    return out


@router.post("/voice/transcribe")
async def voice_transcribe(file: UploadFile = File(...),
                           language: str | None = Form(None),
                           _: None = Depends(require_auth)):
    """Turn one dictation into text. The audio is never stored.

    Only reached by browsers with no speech recognition of their own — where the
    browser can transcribe locally it does, and this server never sees the audio
    at all. A deployment with no transcriber configured answers 501 rather than
    degrading to something that looks like it worked.

    The transcript is returned, not saved. It becomes a case the moment the caller
    posts it to `/cases`, at which point it is sealed under that case's key like
    any other evidence — which is the only reason voice can exist in a product
    that promises provable erasure.
    """
    if speech.server_transcriber() is None:
        raise HTTPException(501, detail={
            "error": "no_server_transcriber",
            "detail": ("This deployment has no server-side speech provider. Use "
                       "your browser's own speech recognition, or type instead."),
            "availability": speech.availability(),
        })
    raw = await file.read()
    try:
        transcript = speech.transcribe(raw, media_type=file.content_type or "",
                                       language=language)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception:
        # Never surface a provider's raw error: it can carry the request URL and
        #, depending on the provider, fragments of the key.
        raise HTTPException(502, "the speech provider could not be reached")

    out = transcript.as_dict()
    out["language_note"] = speech.language_note(transcript.text, transcript.language)
    return out


@router.get("/sentinel")
def sentinel_scan(workspace: str = "default", limit: int = 100):
    """What is stuck, and what Agent X would do about it. Read-only.

    Public like the rest of the read surface: an agent that claims to watch its
    own work has to let you see the result without credentials. Nothing here
    changes a case — `?apply=true` on the POST does, and it is token-gated.
    """
    limit = max(1, min(limit, 200))
    with _conn() as conn:
        return sentinel.sweep(conn, workspace=workspace, apply=False, limit=limit)


class SentinelReq(BaseModel):
    workspace: str = "default"
    apply: bool = False
    limit: int = 100


@router.post("/sentinel/sweep")
def sentinel_sweep(r: SentinelReq, _: None = Depends(require_auth)):
    """Run the sentinel, optionally applying the remediations it is permitted.

    `apply=false` is still the default here. Every remediation is put through
    `governor.assess()` first, so this endpoint cannot perform an action the
    autonomy level would not allow a person to trigger — self-healing is not a
    way around the approval gate, and a stall needing escalation is reported for
    a human rather than acted on.
    """
    limit = max(1, min(r.limit, 200))
    with _conn() as conn:
        return sentinel.sweep(conn, workspace=r.workspace, apply=r.apply,
                              limit=limit)


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


@router.get("/action-center")
def action_center(workspace: str = "default", user_ref: str | None = None,
                  limit: int = 100):
    """The user's operational inbox: everything across their cases that needs
    attention, in one call — derived from live case/approval/question state,
    never a separate notifications table (see engine.action_items)."""
    with _conn() as conn:
        return {"items": engine.action_items(conn, workspace=workspace,
                                             user_ref=user_ref, limit=limit)}


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
    """Attach a file. What could be read is read; the rest is hashed and labelled.

    Plain text and a PDF's own text layer are extracted. A scan, a photograph or
    an encrypted PDF is stored, hashed and listed as evidence with no text layer,
    and the response says which of those it was — so a user can see that Agent X
    holds their document without believing it has read it.

    There is still no OCR, and none is pretended. Transcribing a scan requires a
    hosted vision model, and putting a network call with non-reproducible output
    inside the evidence path would break the property that a case can be re-run
    deterministically. A scan is reported as a scan.
    """
    raw = await file.read()
    name = file.filename or "upload"
    read = documents.extract(raw, name, file.content_type)
    with _conn() as conn:
        try:
            out = engine.attach(conn, case_id, kind=kind, text=read.text, raw=raw,
                                filename=name, media_type=file.content_type)
        except ValueError as e:
            raise HTTPException(400, str(e))
        case = case_mod.get(conn, case_id)
    # How the text was obtained travels with the response, because "we extracted
    # 4,000 characters from a PDF" and "we decoded a text file" warrant different
    # amounts of trust in the facts derived downstream.
    out["extraction"] = read.as_dict()
    # Advisory, never a refusal: the document is already stored and hashed above.
    # Uploading the wrong file fails quietly otherwise — no facts are extracted,
    # the case stays short of evidence, and the user who thinks they supplied it
    # waits for an answer that cannot come.
    out["relevance"] = documents.relevance(
        read.text, (case or {}).get("description") or "", name,
        facts_found=len(out.get("facts") or [])).as_dict()
    if read.note:
        out["note"] = read.note
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
        if not any(e["id"] == evidence_id for e in egraph.list_evidence(conn, case_id)):
            raise HTTPException(404, f"no evidence {evidence_id!r} on case {case_id!r}")
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
