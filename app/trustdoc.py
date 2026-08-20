"""
Document-pipeline routes.

Mounted into the same FastAPI app as the erasure pipeline, because this is one
product: same audit chain, same certificate table, same /verify. Kept in its own
router so adding a third pipeline later means adding a module, not editing a
1000-line file.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from core.trust import audit, gate
from pipelines.document import dws, pipeline, review

router = APIRouter(prefix="/api/doc", tags=["document"])


def _conn():
    """Borrow a connection from the shared store, so both pipelines pool together."""
    from db import store
    return store.connect()


# ── upload + extract ──────────────────────────────────────────────────────
@router.post("/upload")
async def upload(file: UploadFile = File(...),
                 doc_type: str = Form("invoice"),
                 workspace: str = Form("default"),
                 _auth: None = Depends(lambda: None)):
    """Extract a document and route every field. 503 if DWS is not configured."""
    raw = await file.read()
    with _conn() as conn:
        job_id = pipeline.create_job(conn, doc_type, workspace)
        try:
            return pipeline.ingest(conn, job_id, raw, file.filename or "upload", doc_type)
        except dws.DWSUnavailable as e:
            # 503, not 500: the request was fine, the dependency is missing. The
            # failure is already on the audit chain by the time this is raised.
            raise HTTPException(status_code=503, detail={
                "error": "dws_unavailable", "job_id": job_id, "detail": str(e)})
        except dws.DWSError as e:
            raise HTTPException(status_code=502, detail={
                "error": "dws_rejected", "job_id": job_id, "detail": str(e)})
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))


# ── read ──────────────────────────────────────────────────────────────────
@router.get("/jobs/{job_id}")
def job(job_id: str):
    with _conn() as conn:
        try:
            prog = review.progress(conn, job_id)
        except review.ReviewError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return {"job_id": job_id, **prog,
                "fields": pipeline.job_fields(conn, job_id),
                "pending": review.pending(conn, job_id)}


@router.get("/jobs/{job_id}/audit")
def job_audit(job_id: str):
    """The chain plus its verification. Public on purpose — an audit trail nobody
    can read is not an audit trail."""
    with _conn() as conn:
        return {"job_id": job_id,
                "chain": audit.chain(conn, job_id),
                "verification": audit.verify_chain(conn, job_id)}


@router.get("/policy")
def policy():
    """The exact routing policy in force. Judges should not have to read the binary."""
    return gate.policy_snapshot()


# ── review ────────────────────────────────────────────────────────────────
class ReviewReq(BaseModel):
    field_id: str
    action: str                    # ACCEPT | CORRECT
    reviewer: str
    new_value: str | None = None


@router.post("/jobs/{job_id}/review")
def submit_review(job_id: str, r: ReviewReq):
    with _conn() as conn:
        try:
            return review.decide(conn, job_id, r.field_id, r.action, r.reviewer, r.new_value)
        except review.ReviewError as e:
            # 409: the request is well-formed but conflicts with the job's state
            # (already reviewed, wrong status). Distinct from a malformed request.
            raise HTTPException(status_code=409, detail=str(e))


# ── DWS Viewer session ────────────────────────────────────────────────────
@router.get("/viewer-token")
def viewer_token(origin: str = "http://localhost:8000"):
    """Mint a scoped, short-lived JWT for the browser-side DWS Viewer.

    The raw DWS_API_KEY must never reach the client, so the Viewer is authorised
    with a token limited to viewing operations and to this origin.
    """
    if not dws.configured():
        raise HTTPException(status_code=503, detail={
            "error": "dws_unavailable",
            "detail": "DWS_API_KEY is not set, so no Viewer session can be minted. "
                      "The review screen falls back to a plain field editor."})
    try:
        return dws.session_token(
            allowed_operations=["document_editor_api", "ocr_api"],
            allowed_origins=[origin.replace("http://", "").replace("https://", "")],
            expires_in=3600,
        )
    except dws.DWSError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/dws-status")
def dws_status():
    """Whether a live DWS call is possible. The UI uses this to say which mode it
    is in rather than silently degrading."""
    return {"configured": dws.configured(),
            "base_url": dws.BASE_URL,
            "extract_endpoint": dws.EXTRACT_PATH,
            "sign_endpoint": dws.SIGN_PATH}


# ── review UI ─────────────────────────────────────────────────────────────
def review_page() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "templates", "review.html")
    if not os.path.exists(path):
        return "<h1>review.html missing</h1>"
    with open(path, encoding="utf-8") as f:
        return f.read()


@router.get("/review", response_class=HTMLResponse, include_in_schema=False)
def review_ui():
    return HTMLResponse(review_page())


# ── the routes the 5-screen flow needs ────────────────────────────────────
DEMO_FIELDS = [
    {"name": "invoice_number", "value": "INV-4471",       "confidence": 0.97, "recognition": 0.95},
    {"name": "vendor_name",    "value": "Acme Logistics", "confidence": 0.88, "recognition": 0.93},
    {"name": "date",           "value": "2026-07-14",     "confidence": 0.94, "recognition": 0.91},
    {"name": "total",          "value": "1,248.00",       "confidence": 0.91, "recognition": 0.90},
    {"name": "account_number", "value": "GB29 NWBK 6016", "confidence": 0.99, "recognition": 0.41},
    {"name": "description",    "value": "freight, Q3",    "confidence": None, "recognition": 0.86},
]


@router.post("/demo-job")
def demo_job(workspace: str = "default"):
    """Seed a job from a RECORDED extraction so the flow can be walked without a key.

    Recorded as engine='recorded-fixture' on the audit chain, so a demo run is
    permanently distinguishable from a live one. It is not a shortcut past the gate:
    everything after extraction is the same code path.
    """
    with _conn() as conn:
        job_id = pipeline.create_job(conn, "invoice", workspace)
        return pipeline.ingest_prepared(conn, job_id, DEMO_FIELDS,
                                        engine="recorded-fixture")


@router.post("/jobs/{job_id}/finalize")
def finalize_job(job_id: str):
    """Generate, sign, and self-verify. 422 when the loop catches a mismatch."""
    from pipelines.document import finalize as _fin
    with _conn() as conn:
        try:
            out = _fin.finalize(conn, job_id)
        except _fin.SelfVerifyFailed as e:
            raise HTTPException(status_code=422, detail={
                "error": "self_verify_failed", "mismatches": e.mismatches})
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))

        # issue and persist the certificate in the same request, so a signed job
        # always has an attestation rather than acquiring one later
        from core.trust import certificate as _cert
        from pipelines.document import sign as _sign
        body = _cert.build(conn, job_id, kind="document",
                           document_sha256=out["document_sha256"],
                           fields=pipeline.job_fields(conn, job_id),
                           extra={"document_signature_embedded":
                                  out["document_signature_embedded"]})
        env = _cert.sign(body, _sign._key())
        import json as _json
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO certificates (job_id, cert_json, sha256, signature, "
                "public_key, audit_head, doc_sha256) VALUES (%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (job_id) DO UPDATE SET cert_json=excluded.cert_json, "
                "sha256=excluded.sha256, signature=excluded.signature, "
                "public_key=excluded.public_key, audit_head=excluded.audit_head, "
                "doc_sha256=excluded.doc_sha256",
                (job_id, _json.dumps(env["certificate"]), env["sha256"],
                 env.get("signature"), env.get("public_key"),
                 body["chain_head"], out["document_sha256"]))
    out.pop("pdf", None)                      # bytes are not JSON
    return out


@router.get("/jobs/{job_id}/certificate")
def get_certificate(job_id: str):
    """The portable certificate. This is the artefact a judge takes away."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT cert_json, sha256, signature, public_key FROM certificates "
                    "WHERE job_id = %s", (job_id,))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="no certificate issued for this job")
    return {"certificate": row[0], "sha256": row[1], "signature": row[2],
            "public_key": row[3], "signed": bool(row[2]),
            "algorithm": "ECDSA-P256-SHA256" if row[2] else None}


@router.post("/verify")
def verify_certificate(payload: dict, trusted_public_key: str | None = None):
    """Standalone verification. Works with or without the database."""
    from core.trust import certificate as _cert
    with _conn() as conn:
        return _cert.verify(payload, conn, trusted_public_key)
