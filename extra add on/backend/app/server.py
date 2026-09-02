"""SENTINEL API server - the real bridge between the agent engine and the
Next.js frontend. Every route below reads from an actual, live source (the
job queue, the evidence store, the governance registry/gateway log, or a
fresh `hunt()` scan) - nothing here is a static fixture. The Command Center
polls GET /api/state and gets back the fleet's real, current condition every
time, whether that's "idle", "analyst is mid-reasoning on job abc123", or
"resolved, awaiting a human decision at the Deployment Gate."

Run with:
    ./.venv/Scripts/python.exe -m app.server
"""

from __future__ import annotations

import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app import auth, decisions, ledger, orchestrator
from app.agents import hunter as hunter_module
from app.knowledge import advisory_cache
from app.agents.hunter import hunt
from app.config import DEMO_REPO_DIR, DEMO_REPO_URL, GCP_PROJECT_ID
from app.governance import gateway, identity, model_armor, registry
from app.queue import get_queue
from app.store import get_store

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Warm the findings cache in the background at startup.

    A cold scan is a real git clone plus `npm audit` (~14s) plus advisory
    grounding, and whoever loaded the dashboard first used to absorb all of
    it as a single blocking request - on Cloud Run, potentially past the
    request timeout on the very first hit after a cold start.

    Doing it on a daemon thread means the API answers immediately and the
    scan lands a few seconds later; the UI polls, so it fills in on its own.
    Failures are deliberately swallowed: a warm-up is an optimisation, and
    the normal on-demand path in _load_findings still runs (and still
    reports errors properly) if this never completes.
    """
    def _warm() -> None:
        try:
            _load_findings()
        except Exception as exc:  # noqa: BLE001 - never block startup on a scan
            print(f"[startup] findings warm-up failed, will scan on demand: {exc}")

    threading.Thread(target=_warm, name="findings-warmup", daemon=True).start()
    yield


app = FastAPI(title="SENTINEL Agent Engine API", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("SENTINEL_CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------------------------------
# Findings cache - hunt() shells out to git clone + npm audit, which is real
# but too slow to re-run on every poll. Cache in memory; POST /api/findings
# /refresh forces a real re-scan on demand.
# --------------------------------------------------------------------------
_findings_lock = Lock()
_findings_cache: list[dict] | None = None
_findings_loaded_at: str | None = None


def _load_findings(force: bool = False) -> list[dict]:
    """Returns the cached grounded findings, scanning if needed.

    A *degraded* scan - one where OSV/NVD/GHSA lookups failed rather than
    genuinely not matching - is deliberately not cached. A transient DNS
    blip during the first scan would otherwise pin an empty result for the
    process lifetime and blank the whole dashboard with no explanation,
    which is exactly what happened in testing. Not caching it means the
    next poll simply retries.
    """
    global _findings_cache, _findings_loaded_at
    with _findings_lock:
        if _findings_cache is None or force:
            findings = hunt(DEMO_REPO_DIR) if DEMO_REPO_DIR.exists() else hunt()
            fresh = [f.model_dump(mode="json") for f in findings]
            if hunter_module.last_scan.degraded:
                # Serve whatever we already had rather than overwriting good
                # data with an under-reporting scan; retry on the next call.
                return _findings_cache or fresh
            _findings_cache = fresh
            _findings_loaded_at = datetime.now(timezone.utc).isoformat()
        return _findings_cache


def _find_finding(finding_id: str) -> dict | None:
    return next((f for f in _load_findings() if f["finding_id"] == finding_id), None)


def _default_finding_id() -> str | None:
    findings = _load_findings()
    if not findings:
        return None
    jwt_finding = next((f for f in findings if f["component"] == "jsonwebtoken"), None)
    return (jwt_finding or findings[0])["finding_id"]


def _hhmmss(iso_ts: str | None) -> str:
    if not iso_ts:
        return datetime.now(timezone.utc).strftime("%H:%M:%S")
    try:
        return datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).strftime("%H:%M:%S")
    except ValueError:
        return iso_ts[:8]


# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------


@app.get("/api/findings")
def list_findings():
    return {"findings": _load_findings()}


@app.post("/api/findings/refresh")
def refresh_findings(principal: str = Depends(auth.require_principal)):
    return {"findings": _load_findings(force=True)}


@app.get("/api/findings/{finding_id}")
def get_finding(finding_id: str):
    finding = _find_finding(finding_id)
    if finding is None:
        raise HTTPException(404, f"No such finding: {finding_id}")
    return finding


# --------------------------------------------------------------------------
# Jobs (investigations)
# --------------------------------------------------------------------------


class StartInvestigationRequest(BaseModel):
    finding_id: str | None = None


_investigation_lock = Lock()


# An investigation legitimately runs for 10-15 minutes (real clone, real
# npm audit, real sandboxed exploit, several Gemini calls), so this has to
# be comfortably longer than a slow-but-healthy run or we would kill live
# work. It only needs to be short enough that a dead worker's claim frees
# up well within a demo.
STALE_JOB_MINUTES = int(os.environ.get("SENTINEL_STALE_JOB_MINUTES", 45))


def _is_stale(job) -> bool:
    """True when a job claims to be running but has shown no progress for
    longer than the lease, so whatever claimed it is presumed gone."""
    if job.status not in ("queued", "running"):
        return False
    try:
        updated = datetime.fromisoformat(job.updated_at)
    except (TypeError, ValueError):
        return False
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    age_minutes = (datetime.now(timezone.utc) - updated).total_seconds() / 60
    return age_minutes > STALE_JOB_MINUTES


@app.post("/api/investigations")
def start_investigation(
    req: StartInvestigationRequest, principal: str = Depends(auth.require_principal)
):
    finding_id = req.finding_id or _default_finding_id()
    if finding_id is None:
        raise HTTPException(400, "No findings available to investigate.")
    if _find_finding(finding_id) is None:
        raise HTTPException(404, f"No such finding: {finding_id}")

    # A real lock, not just a check - without it, two near-simultaneous
    # requests (e.g. a slow request the client gave up on but the server
    # kept processing, followed by a retry) can both observe "no existing
    # job" before either has enqueued one, and both proceed to enqueue a
    # duplicate investigation for the same finding.
    with _investigation_lock:
        queue = get_queue()
        existing = [
            j for j in queue.list_jobs(limit=50)
            if j.payload.get("finding_id") == finding_id and j.status in ("queued", "running")
        ]
        # Reclaim jobs whose worker died mid-run. A "running" job is only
        # meaningful while some process is actually running it; when a
        # worker is killed (a restart, a crashed Cloud Run instance) its
        # claim is never released, and this dedup check would then keep
        # handing that dead job back forever - wedging that finding so it
        # can never be investigated again.
        live = []
        for job in existing:
            if _is_stale(job):
                queue.fail(
                    job.job_id,
                    f"Reclaimed: no progress for over {STALE_JOB_MINUTES} minutes, "
                    "the worker holding this job is presumed dead.",
                )
            else:
                live.append(job)
        if live:
            return _job_to_dict(live[0])

        job = queue.enqueue("investigate_finding", {"finding_id": finding_id})
        return _job_to_dict(job)


@app.post("/api/jobs/{job_id}/abort")
def abort_job(job_id: str, principal: str = Depends(auth.require_principal)):
    queue = get_queue()
    job = queue.get(job_id)
    if job is None:
        raise HTTPException(404, f"No such job: {job_id}")
    if job.status not in ("queued", "running"):
        raise HTTPException(400, f"Job {job_id} is already {job.status}, nothing to abort.")
    queue.fail(job_id, "Aborted by operator via Command Center.")
    return _job_to_dict(queue.get(job_id))


@app.get("/api/jobs")
def list_jobs(limit: int = 50):
    return {"jobs": [_job_to_dict(j) for j in get_queue().list_jobs(limit=limit)]}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = get_queue().get(job_id)
    if job is None:
        raise HTTPException(404, f"No such job: {job_id}")
    return _job_to_dict(job)


def _job_to_dict(job) -> dict:
    return {
        "job_id": job.job_id,
        "job_type": job.job_type,
        "payload": job.payload,
        "status": job.status,
        "result": job.result,
        "error": job.error,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


# --------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------


@app.get("/api/evidence")
def list_evidence():
    return {"evidence": get_store().list_evidence()}


@app.get("/api/evidence/{finding_id}")
def get_evidence(finding_id: str):
    doc = get_store().get_evidence(finding_id)
    if doc is None:
        raise HTTPException(404, f"No evidence sealed yet for {finding_id}")
    return doc


@app.get("/api/evidence/{finding_id}/verify")
def verify_evidence(finding_id: str):
    """Verifies both seals a sealed record can carry, independently.

    They attest different things and can disagree, which is the point:

    - content signature: SHA-256 recomputed over the record's own canonical
      JSON. Catches any edit to the stored data. Self-contained and offline.
    - DWS seal: the digest of the CAdES-signed PDF that Nutrient issued,
      recomputed from the artifact still on disk. Catches the signed
      document being swapped, truncated, or deleted after the fact.

    A record whose JSON still verifies but whose signed PDF is missing or
    altered is a materially different situation from one where both hold,
    and a reader deserves to be told which.
    """
    import hashlib

    from app.agents.evidence_agent import EVIDENCE_DIR, signed_pdf_path, verify_signature

    doc = get_store().get_evidence(finding_id)
    if doc is None:
        raise HTTPException(404, f"No evidence sealed yet for {finding_id}")

    content_valid = verify_signature(doc)

    dws_seal = doc.get("dws_seal")
    # Resolve the artifact *this record* was sealed with, not merely the
    # newest file for this finding - otherwise re-investigating a finding
    # makes every earlier record report a mismatch indistinguishable from
    # real tampering.
    signed_pdf = signed_pdf_path(finding_id, dws_seal)
    if not dws_seal:
        dws = {"present": False, "valid": None, "reason": "no DWS seal on this record"}
    elif signed_pdf is None:
        dws = {"present": True, "valid": False, "reason": "signed PDF is missing from the evidence store"}
    else:
        actual = f"dws:sha256:{hashlib.sha256(signed_pdf.read_bytes()).hexdigest()}"
        dws = {
            "present": True,
            "valid": actual == dws_seal,
            "recomputed": actual,
            "bytes": signed_pdf.stat().st_size,
            "reason": None if actual == dws_seal else "signed PDF does not match the recorded seal",
        }

    return {
        "finding_id": finding_id,
        # Overall validity requires every seal the record claims to hold.
        "valid": content_valid and (dws["valid"] is not False),
        "content_signature": {"valid": content_valid, "signature": doc.get("signature")},
        "dws": {**dws, "seal": dws_seal},
    }


@app.get("/api/evidence/{finding_id}/document")
def get_evidence_document(finding_id: str, variant: str = "signed", download: bool = False):
    """Serves the real Evidence Report PDF so the signed artifact is
    actually reachable. Producing a certificate-signed document and then
    giving nobody a way to open it defeats the purpose - the whole claim is
    that a third party can verify this in ordinary PDF tooling.

    variant=signed   the CAdES-signed PDF Nutrient DWS returned
    variant=unsigned the rendered report before signing
    download=true    force a save instead of inline display. The HTML
                     `download` attribute is ignored cross-origin, and the
                     dashboard and API are on different ports, so the
                     disposition has to come from the server.
    """
    from fastapi.responses import FileResponse

    from app.agents.evidence_agent import EVIDENCE_DIR, signed_pdf_path

    if variant not in ("signed", "unsigned"):
        raise HTTPException(400, "variant must be 'signed' or 'unsigned'")

    suffix = ".signed.pdf" if variant == "signed" else ".pdf"
    if variant == "signed":
        # Same resolver the verify endpoint uses, so the bytes a reader
        # downloads are exactly the bytes that were checked against the seal.
        doc = get_store().get_evidence(finding_id) or {}
        path = signed_pdf_path(finding_id, doc.get("dws_seal"))
    else:
        candidate = EVIDENCE_DIR / f"{finding_id}{suffix}"
        path = candidate if candidate.exists() else None
    if path is None:
        raise HTTPException(
            404,
            f"No {variant} PDF for {finding_id}. PDFs are produced by the Nutrient DWS "
            "sealing step, which requires NUTRIENT_API_KEY to be configured.",
        )
    disposition = "attachment" if download else "inline"
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={"Content-Disposition": f'{disposition}; filename="EVIDENCE-{finding_id}{suffix}"'},
    )


@app.get("/api/deployment-gate/pending")
def list_pending_gate_reviews():
    """Real review queue: every sealed evidence record that doesn't yet
    have a human decision recorded. No separate 'review task' storage
    exists or is needed - this is just the honest intersection of two
    things that are already real: the evidence store and decisions.json."""
    pending = []
    for doc in get_store().list_evidence():
        finding_id = doc.get("finding_id")
        if not doc.get("signature") or not finding_id:
            continue
        if decisions.get_decision(finding_id) is not None:
            continue
        finding = _find_finding(finding_id)
        timeline = doc.get("timeline") or []
        pending.append({
            "finding_id": finding_id,
            "title": (finding.get("summary") or finding.get("component")) if finding else finding_id,
            "submitted_at": timeline[-1]["ts"] if timeline else doc.get("checked_at", ""),
            "sealed": True,
        })
    pending.sort(key=lambda p: p["submitted_at"])
    return {"pending": pending}


@app.get("/api/deployment-gate")
def get_deployment_gate(finding_id: str | None = None):
    """Real aggregate for the Deployment Gate: the finding, its sealed
    evidence (if any), the human decision (if any), and a checklist derived
    from actual pipeline output - no hardcoded 'verified'/'1/1 passed'
    props. There's no real regression-test runner anywhere in this system,
    so rather than fabricate a pass/fail count, the checklist reports the
    real, honest thing that exists: how many test files Patch Forge
    actually generated."""
    finding_id = finding_id or _default_finding_id()
    finding = _find_finding(finding_id) if finding_id else None
    evidence = get_store().get_evidence(finding_id) if finding_id else None
    decision = decisions.get_decision(finding_id) if finding_id else None

    verification_results = (evidence or {}).get("verification_results") or []
    patch_proposal = (evidence or {}).get("patch_proposal")
    final_status = (evidence or {}).get("final_status")

    checklist = {
        "security_resolved": final_status == "RESOLVED",
        "security_status": final_status or "not yet investigated",
        "generated_test_count": len((patch_proposal or {}).get("generated_test_paths") or []),
        "reverification_passed": bool(verification_results) and verification_results[-1]["result"] == "RESOLVED",
        "reverification_result": verification_results[-1]["result"] if verification_results else None,
    }

    return {
        "finding": (
            {
                "finding_id": finding["finding_id"],
                "cve": finding.get("advisory_id") or finding["finding_id"],
                "title": finding.get("summary") or finding["component"],
                "component": finding["component"],
                "severity": finding["severity"],
            }
            if finding
            else None
        ),
        "repo": (evidence or {}).get("repo"),
        "commit": (evidence or {}).get("commit"),
        "branchName": (patch_proposal or {}).get("branch_name"),
        "signature": (evidence or {}).get("signature"),
        "sealed": bool((evidence or {}).get("signature")),
        "checklist": checklist,
        "decision": decision,
    }


# --------------------------------------------------------------------------
# Governance
# --------------------------------------------------------------------------


@app.get("/api/registry")
def get_registry():
    from dataclasses import asdict

    return {"agents": [asdict(a) for a in registry.list_agents()]}


@app.get("/api/gateway-log")
def get_gateway_log(limit: int = 200):
    return {"log": gateway.read_log(limit=limit)}


@app.get("/api/model-armor-log")
def get_model_armor_log(limit: int = 200):
    return {"log": model_armor.read_log(limit=limit)}


class PolicyEvalRequest(BaseModel):
    agent: str
    action: str


@app.post("/api/policy/evaluate")
def evaluate_policy(req: PolicyEvalRequest):
    """Runs the real identity.evaluate() - the exact function the live
    Gateway's permission check is built on (see governance/identity.py) -
    so the Governance page's simulator is genuinely testing the same policy
    code that governs live tool calls, not a parallel reimplementation."""
    agent_id = req.agent.strip().lower()
    action = req.action.strip()
    decision, reason = identity.evaluate(agent_id, action)
    approved = registry.is_approved(agent_id)
    if decision == "allowed" and not approved:
        decision, reason = "blocked", f"agent '{agent_id}' is not approved in the Agent Registry"
    return {"agent": agent_id, "action": action, "decision": decision, "reason": reason}


@app.get("/api/system-info")
def system_info():
    return {
        "orchestrator": orchestrator.active_orchestrator(),
        "queue_backend": os.environ.get("SENTINEL_QUEUE_BACKEND", "local"),
        "store_backend": os.environ.get("SENTINEL_STORE_BACKEND", "local"),
        "gcp_project_id": GCP_PROJECT_ID,
        "demo_repo_url": DEMO_REPO_URL,
        "nutrient_configured": bool(os.environ.get("NUTRIENT_API_KEY")),
        "gemini_configured": bool(os.environ.get("GEMINI_API_KEY")),
        "github_configured": bool(os.environ.get("GITHUB_TOKEN")),
        # Surfaced so the security posture is visible rather than assumed.
        # When false, mutating endpoints accept unauthenticated calls and
        # decisions are attributed to an explicitly-labelled dev principal.
        "auth_enabled": auth.auth_enabled(),
    }


# --------------------------------------------------------------------------
# Deployment Gate decisions (approve/reject)
# --------------------------------------------------------------------------


class DecisionRequest(BaseModel):
    finding_id: str
    decision: str  # "approved" | "rejected"


@app.post("/api/decisions")
def post_decision(req: DecisionRequest, principal: str = Depends(auth.require_principal)):
    """Records a human Deployment Gate decision.

    The actor is the *authenticated* principal, never a client-supplied
    field - this endpoint is the point where a person takes responsibility
    for shipping a patch, so the name written into the evidence record and
    the audit ledger has to be one the server verified.
    """
    try:
        return decisions.set_decision(req.finding_id, req.decision, principal)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/decisions/{finding_id}")
def get_decision(finding_id: str):
    record = decisions.get_decision(finding_id)
    return record or {"finding_id": finding_id, "decision": None}


# --------------------------------------------------------------------------
# Command Center aggregate state - the one endpoint the dashboard polls.
# --------------------------------------------------------------------------

_AGENT_META = {
    "hunter": {"name": "Hunter", "version": "v1.0"},
    "analyst": {"name": "Analyst", "version": "v1.0"},
    "verifier": {"name": "Verification Lab", "version": "v1.0"},
    "patch-forge": {"name": "Patch Forge", "version": "v1.0"},
    "re-verifier": {"name": "Re-Verifier", "version": "v1.0"},
    "watchdog": {"name": "Watchdog", "version": "v0.1"},
}
_BASE_MEM_PCT = {"hunter": 4, "analyst": 6, "verifier": 9, "patch-forge": 3, "re-verifier": 5, "watchdog": 1}
_STAGE_TASK = {
    "analyst": "assessing relevance",
    "verifier": "executing sandbox scenario",
    "patch-forge": "generating remediation",
    "re-verifier": "re-verifying fix branch",
}
_STAGE_PROGRESS = {"analyst": 25, "verifier": 40, "patch-forge": 60, "re-verifier": 80}
_STAGE_EDGES = {
    "analyst": ["e-hunter-analyst", "e-hub-analyst"],
    "verifier": ["e-analyst-verifier", "e-hub-verifier"],
    "patch-forge": ["e-verifier-patch", "e-hub-patch"],
    "re-verifier": ["e-patch-reverifier", "e-hub-reverifier"],
}
_RESULT_TO_STATE = {"RESOLVED": "RESOLVED", "CONFIRMED_EXPLOITABLE": "EXPLOITABLE", "INCONCLUSIVE": "VERIFIED"}
_RESULT_TO_LEVEL = {"RESOLVED": "info", "CONFIRMED_EXPLOITABLE": "error", "INCONCLUSIVE": "warn"}


def _most_recent_job_for(finding_id: str):
    jobs = [j for j in get_queue().list_jobs(limit=100) if j.payload.get("finding_id") == finding_id]
    return jobs[0] if jobs else None  # list_jobs already returns newest-first


def _current_agent_from_log(since_iso: str) -> str | None:
    """Real, honest inference: the most recent gateway-logged agent action
    at or after the job's creation time, restricted to mid-pipeline agents.
    This is what actually drives the "which node is glowing" state - not a
    fabricated progress bar."""
    log = gateway.read_log(limit=500)
    for entry in reversed(log):
        if entry.get("ts", "") < since_iso:
            continue
        agent = entry.get("agent")
        if agent in _STAGE_TASK:
            return agent
    return None


@app.get("/api/state")
def get_state(finding_id: str | None = None):
    findings = _load_findings()
    finding_id = finding_id or _default_finding_id()
    finding = _find_finding(finding_id) if finding_id else None

    job = _most_recent_job_for(finding_id) if finding_id else None
    evidence = get_store().get_evidence(finding_id) if finding_id else None
    decision = decisions.get_decision(finding_id) if finding_id else None

    # --- derive current stage / active agent ---
    current_agent = "hunter"
    stage_task = "idle - awaiting investigation start"
    progress = 0
    active_edge_ids: list[str] = ["e-github-vuln", "e-vuln-hub"]
    last_reverify_results: list[dict] = []
    verdict: dict | None = None

    if job is not None:
        if job.status == "queued":
            current_agent, stage_task, progress = "hunter", "queued - awaiting worker claim", 10
        elif job.status == "running":
            inferred = _current_agent_from_log(job.created_at) or "analyst"
            current_agent = inferred
            stage_task = _STAGE_TASK.get(inferred, "processing")
            progress = _STAGE_PROGRESS.get(inferred, 20)
            active_edge_ids = _STAGE_EDGES.get(inferred, active_edge_ids)
        elif job.status == "done":
            current_agent, stage_task, progress = "re-verifier", "awaiting Deployment Gate decision", 100
            active_edge_ids = ["e-patch-reverifier", "e-reverifier-watchdog"]
            result = job.result or {}
            verdict = result.get("verdict")
            last_reverify_results = (result.get("reverify") or {}).get("results", [])
        elif job.status == "failed":
            current_agent, stage_task, progress = "watchdog", f"FAILED: {job.error}", 50
            active_edge_ids = ["e-verifier-watchdog"]

    # --- agents table ---
    approved = {a.id: a.status for a in registry.list_agents()}
    log_tail = gateway.read_log(limit=300)
    agents_out = []
    for agent_id, meta in _AGENT_META.items():
        count = sum(1 for e in log_tail if e.get("agent") == agent_id)
        status = "idle"
        if job is not None and job.status == "failed" and agent_id == "watchdog":
            status = "error"
        elif agent_id == current_agent:
            status = "active"
        agents_out.append(
            {
                "id": agent_id,
                "name": meta["name"],
                "version": meta["version"],
                "status": status,
                "health": 100 if approved.get(agent_id) == "approved" else 55,
                "memoryPct": min(99, _BASE_MEM_PCT[agent_id] + count),
            }
        )

    # --- graph nodes/edges (static topology, live "active" flag) ---
    graph_nodes = [
        {"id": "github", "label": "GitHub Ingestion", "type": "source"},
        {
            "id": "vulnerability",
            "label": (finding or {}).get("advisory_id") or "no active finding",
            "sublabel": "Vulnerability",
            "type": "finding",
        },
        {"id": "hub", "label": "Automated Agents", "type": "hub"},
        {"id": "hunter", "label": "Hunter", "type": "agent", "agentId": "hunter", "active": current_agent == "hunter"},
        {"id": "analyst", "label": "Analyst", "type": "agent", "agentId": "analyst", "active": current_agent == "analyst"},
        {"id": "verifier", "label": "Verification Lab", "type": "agent", "agentId": "verifier", "active": current_agent == "verifier"},
        {"id": "patch-forge", "label": "Patch Forge", "type": "agent", "agentId": "patch-forge", "active": current_agent == "patch-forge"},
        {"id": "re-verifier", "label": "Re-Verifier", "type": "agent", "agentId": "re-verifier", "active": current_agent == "re-verifier"},
        {"id": "watchdog", "label": "Watchdog", "type": "agent", "agentId": "watchdog", "active": current_agent == "watchdog"},
    ]
    advisory = (finding or {}).get("advisory_id") or "no active finding"
    component = (finding or {}).get("component", "")
    verify_msg = (
        f"{last_reverify_results[-1]['scenario']} -> {last_reverify_results[-1]['result']}"
        if last_reverify_results
        else "reproduce condition in sandbox"
    )
    edge_messages = {
        "e-github-vuln": f"real npm audit scan: {DEMO_REPO_URL}",
        "e-vuln-hub": f"finding {finding_id or '(none)'} dispatched to fleet" if finding_id else "awaiting a finding to dispatch",
        "e-hub-hunter": "scan manifests for known advisories",
        "e-hub-analyst": f"assess reachability of {advisory}",
        "e-hub-verifier": verify_msg,
        "e-hub-patch": "generate remediation",
        "e-hub-reverifier": "re-run scenario against fix branch",
        "e-hub-watchdog": "monitoring fleet health",
        "e-hunter-analyst": f"component: {component}@{(finding or {}).get('version', '')}" if finding else "",
        "e-analyst-verifier": f"relevance: {verdict['verdict']}" if verdict else "awaiting relevance verdict",
        "e-verifier-patch": verify_msg,
        "e-patch-reverifier": "patch + version bump ready" if last_reverify_results else "patch pending",
        "e-reverifier-watchdog": verify_msg if job and job.status == "done" else "awaiting re-verification",
        "e-verifier-watchdog": "sandbox heartbeat",
    }
    graph_edges = [
        {"id": eid, "source": src, "target": tgt, "lastMessage": edge_messages.get(eid, "")}
        for eid, src, tgt in [
            ("e-github-vuln", "github", "vulnerability"),
            ("e-vuln-hub", "vulnerability", "hub"),
            ("e-hub-hunter", "hub", "hunter"),
            ("e-hub-analyst", "hub", "analyst"),
            ("e-hub-verifier", "hub", "verifier"),
            ("e-hub-patch", "hub", "patch-forge"),
            ("e-hub-reverifier", "hub", "re-verifier"),
            ("e-hub-watchdog", "hub", "watchdog"),
            ("e-hunter-analyst", "hunter", "analyst"),
            ("e-analyst-verifier", "analyst", "verifier"),
            ("e-verifier-patch", "verifier", "patch-forge"),
            ("e-patch-reverifier", "patch-forge", "re-verifier"),
            ("e-reverifier-watchdog", "re-verifier", "watchdog"),
            ("e-verifier-watchdog", "verifier", "watchdog"),
        ]
    ]

    # --- verification log lines (real, derived from finding + verdict + reverify results + gateway log) ---
    verification_log = []
    if finding:
        verification_log.append(
            {
                "id": "l-detect",
                "ts": _hhmmss(job.created_at if job else None),
                "level": "info",
                "text": f"npm audit detected {finding.get('advisory_id')} in {finding['component']}@{finding['version']}",
            }
        )
    if verdict:
        verification_log.append(
            {
                "id": "l-verdict",
                "ts": _hhmmss(job.updated_at if job else None),
                "level": "info",
                "text": f"relevance: {verdict['verdict']} - {verdict['reasoning']}",
            }
        )
    for i, r in enumerate(last_reverify_results):
        verification_log.append(
            {
                "id": f"l-verify-{i}",
                "ts": _hhmmss(job.updated_at if job else None),
                "level": _RESULT_TO_LEVEL.get(r["result"], "info"),
                "text": (
                    f"{r['scenario']}: expected {r['expected']}, observed {r['observed']} -> {r['result']} "
                    f"(sandbox {r['sandbox_id']}, {r['duration_ms']}ms)"
                ),
            }
        )
    if job is not None and job.status == "running":
        recent = [e for e in log_tail if e.get("ts", "") >= job.created_at][-6:]
        for i, e in enumerate(recent):
            verification_log.append(
                {
                    "id": f"l-gw-{i}",
                    "ts": _hhmmss(e.get("ts")),
                    "level": "warn" if e.get("decision") == "blocked" else "info",
                    "text": f"{e['agent']}: {e['action']} -> {e['decision']}",
                }
            )
    if not verification_log:
        verification_log.append(
            {"id": "l-empty", "ts": _hhmmss(None), "level": "info", "text": "No investigation has been started yet."}
        )

    # --- replay timeline ---
    has_verify = bool(last_reverify_results) or current_agent in ("verifier", "patch-forge", "re-verifier")
    has_patch = bool(last_reverify_results) or current_agent in ("patch-forge", "re-verifier")
    has_reverify = bool(last_reverify_results) or current_agent == "re-verifier" and job and job.status == "done"
    replay_steps = [
        {"id": "discovery", "label": "discovery", "ts": _hhmmss(None) if finding else "--:--:--", "status": "done" if finding else "pending"},
        {"id": "verification", "label": "verification", "ts": _hhmmss(job.updated_at if job else None), "status": "done" if has_verify else ("active" if job and job.status == "running" else "pending")},
        {"id": "patch", "label": "patch", "ts": _hhmmss(job.updated_at if job else None), "status": "done" if has_patch else ("active" if current_agent == "patch-forge" else "pending")},
        {"id": "re-verify", "label": "re-verify", "ts": _hhmmss(job.updated_at if job else None), "status": "done" if last_reverify_results else ("active" if current_agent == "re-verifier" else "pending")},
        {"id": "resolution", "label": "resolution", "ts": _hhmmss(job.updated_at if job else None), "status": "active" if (job and job.status == "done") else "pending"},
    ]

    # --- evidence vault doc ---
    evidence_doc = None
    if evidence:
        timeline = evidence.get("timeline") or []
        evidence_doc = {
            "filename": f"EVIDENCE-{finding_id}.json",
            "hash": evidence.get("signature") or evidence.get("dws_seal") or "unsigned",
            "timestamp": timeline[-1]["ts"] if timeline else evidence.get("ts", ""),
            "sealed": bool(evidence.get("signature")),
            # Reported separately so the UI can say which seal it actually
            # has, rather than claiming a Nutrient DWS seal that is only
            # issued when NUTRIENT_API_KEY is configured.
            "dwsSealed": bool(evidence.get("dws_seal")),
            "reviewStatus": (decision or {}).get("decision") or "pending",
        }

    # --- verification state card ---
    if last_reverify_results:
        final_result = last_reverify_results[-1]
        verification_state = {
            "status": _RESULT_TO_STATE.get(final_result["result"], "VERIFIED"),
            "assertion": f"assert {final_result['expected']} -> {final_result['observed']}",
            "progressPct": progress,
            "activeAgent": current_agent,
            "activeTask": stage_task,
        }
    else:
        verification_state = {
            "status": "PENDING",
            "assertion": "no verification scenario has completed yet",
            "progressPct": progress,
            "activeAgent": current_agent,
            "activeTask": stage_task,
        }

    return {
        "finding": (
            {"id": finding["finding_id"], "cve": finding.get("advisory_id") or finding["finding_id"], "severity": finding["severity"]}
            if finding
            else None
        ),
        "findingOptions": [{"id": f["finding_id"], "cve": f.get("advisory_id") or f["finding_id"], "severity": f["severity"], "component": f["component"]} for f in findings],
        "job": _job_to_dict(job) if job else None,
        "agents": agents_out,
        "graphNodes": graph_nodes,
        "graphEdges": graph_edges,
        "activeEdgeIds": active_edge_ids,
        "verificationLog": verification_log,
        "replaySteps": replay_steps,
        "evidenceDoc": evidence_doc,
        "verificationState": verification_state,
    }


# --------------------------------------------------------------------------
# Audit Ledger - a real SHA-256 hash chain built from actual pipeline events
# (Hunter's real findings, plus every real timeline entry already sealed
# into an evidence record). Computed fresh from the real store on each
# request rather than incrementally maintained, so it's always consistent
# with whatever the store actually holds and never drifts from it - the
# chain is a derived view, not a second copy of the truth.
# --------------------------------------------------------------------------

def _build_ledger() -> list[dict]:
    findings = _load_findings()
    discovered_at = _findings_loaded_at or datetime.now(timezone.utc).isoformat()

    raw_events: list[tuple[str, str, str, str, str, str]] = []  # (ts, finding_id, title, agent, action, detail)

    for f in findings:
        title = f"{f['component']} - {f.get('advisory_id') or f['finding_id']}"
        raw_events.append((
            discovered_at,
            f["finding_id"],
            title,
            "hunter",
            "ingestion verified",
            f"Detected {f.get('advisory_id')} in {f['component']}@{f['version']} (severity: {f['severity']})",
        ))

    for evidence in get_store().list_evidence():
        finding_id = evidence.get("finding_id", "unknown")
        finding = _find_finding(finding_id)
        title = f"{finding['component']} - {finding.get('advisory_id')}" if finding else finding_id
        for entry in evidence.get("timeline", []):
            agent = entry.get("actor", "unknown").lower().replace(" ", "-")
            raw_events.append((entry.get("ts", discovered_at), finding_id, title, agent, "pipeline event", entry.get("action", "")))
        decision = decisions.get_decision(finding_id)
        if decision:
            raw_events.append((
                decision["ts"], finding_id, title, "human",
                f"final {decision['decision']}",
                f"Deployment Gate decision: {decision['decision']} by {decision['actor']}",
            ))

    raw_events.sort(key=lambda e: e[0])

    chain: list[dict] = []
    prev_hash = ledger.GENESIS
    for seq, (ts, finding_id, title, agent, action, detail) in enumerate(raw_events):
        payload = ledger.ledger_payload(finding_id, agent, action, detail, ts)
        entry_hash = ledger.chain_hash(prev_hash, payload)
        chain.append({
            "seq": seq,
            "findingId": finding_id,
            "title": title,
            "agent": agent,
            "action": action,
            "detail": detail,
            "timestamp": ts,
            "hash": entry_hash,
            "prevHash": prev_hash,
        })
        prev_hash = entry_hash

    return chain


@app.get("/api/ledger")
def get_ledger():
    return {"entries": _build_ledger()}


@app.get("/api/health")
def get_health():
    """Real system health: memory-bank collection counts (are they even
    queryable right now) and evidence-store signature integrity (does every
    sealed record's signature still match its content) - the concrete data
    behind the Audit Persistence Monitor panel, replacing what used to be
    three hardcoded module-level constants."""
    from app.agents.evidence_agent import verify_signature

    # Imported here rather than at module scope: app.memory pulls in
    # ChromaDB + ONNX, and the API should still start and serve every other
    # route if the vector store is unavailable, rather than failing to boot.
    try:
        from app import memory

        mem_health = memory.memory_bank_health()
    except Exception as exc:  # noqa: BLE001 - report it, don't fail the probe
        mem_health = {"healthy": False, "collections": {}, "error": str(exc)}
    all_evidence = get_store().list_evidence()
    verified = [verify_signature(e) for e in all_evidence]
    integrity_pct = (sum(verified) / len(verified) * 100) if verified else 100.0

    scan = hunter_module.last_scan
    return {
        "scan": {
            "raw": scan.raw,
            "grounded": scan.grounded,
            "unresolved": scan.unresolved,
            "errored": scan.errored,
            "degraded": scan.degraded,
            # A scan served wholly from cache is complete and correct, but it
            # means the live knowledge sources were never contacted - which is
            # how an upstream OSV/NVD outage would otherwise hide in plain sight.
            "from_cache": scan.from_cache,
            "served_entirely_from_cache": scan.served_entirely_from_cache,
        },
        "advisory_cache": advisory_cache.stats(),
        "memory_bank": mem_health,
        "evidence_integrity_pct": round(integrity_pct, 1),
        "evidence_count": len(all_evidence),
        "evidence_verified_count": sum(verified),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


# --------------------------------------------------------------------------
# Alerts - the real backing for Watchdog's registered capabilities
# (read_agent_logs, raise_alert). Watchdog previously existed only as a
# visual node on the agent graph and a registry row marked "in_review" -
# these are the real signals an actual watchdog process would surface:
# Model Armor blocks, Gateway policy blocks, and failed investigation jobs.
# Nothing here is synthesized - every alert is derived from an event that
# already happened and was already persisted somewhere else.
# --------------------------------------------------------------------------


@app.get("/api/alerts")
def get_alerts(limit: int = 100):
    alerts: list[dict] = []

    for entry in model_armor.read_log(limit=limit):
        if entry.get("severity") == "blocked":
            alerts.append({
                "id": f"armor:{entry['ts']}:{entry['agent']}",
                "ts": entry["ts"],
                "severity": "critical",
                "source": "model-armor",
                "agent": entry["agent"],
                "title": "Prompt injection or unsafe content blocked",
                "detail": entry["text"],
            })

    for entry in gateway.read_log(limit=limit):
        if entry.get("decision") == "blocked":
            alerts.append({
                "id": f"gateway:{entry['ts']}:{entry['agent']}:{entry['action']}",
                "ts": entry["ts"],
                "severity": "warning",
                "source": "gateway",
                "agent": entry["agent"],
                "title": f"Blocked: '{entry['agent']}' attempted '{entry['action']}'",
                "detail": entry["reason"],
            })

    for job in get_queue().list_jobs(limit=limit):
        if job.status == "failed":
            finding_id = job.payload.get("finding_id", "unknown finding")
            alerts.append({
                "id": f"job:{job.job_id}",
                "ts": job.updated_at,
                "severity": "warning",
                "source": "worker",
                "agent": str(finding_id),
                "title": f"Investigation failed for {finding_id}",
                "detail": job.error or "unknown error",
            })

    alerts.sort(key=lambda a: a["ts"], reverse=True)
    return {"alerts": alerts[:limit], "critical_count": sum(1 for a in alerts if a["severity"] == "critical")}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
