"""
Document pipeline — upload, extract, route, record.

The pipeline owns no trust machinery of its own. It calls DWS for the document work
and then hands everything to the shared spine: core.trust.gate decides machine vs
human, core.trust.audit records every step on the same hash chain the erasure
pipeline writes to. That is what makes this one product rather than two sharing a
repository.

Every state change is written to the chain BEFORE the work that follows depends on
it, so a crash mid-run leaves a chain that says exactly how far it got.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from core.trust import audit, gate
from . import dws

# The schema handed to DWS. Deliberately small and invoice-shaped for the demo;
# `doc_type` selects it so other document types can be added without touching the
# pipeline.
SCHEMAS: dict[str, dict[str, Any]] = {
    "invoice": {
        "type": "object",
        "properties": {
            "invoice_number": {"type": "string"},
            "date":           {"type": "string"},
            "due_date":       {"type": "string"},
            "vendor_name":    {"type": "string"},
            "account_number": {"type": "string"},
            "total":          {"type": "string"},
            "tax":            {"type": "string"},
            "address":        {"type": "string"},
        },
        "required": ["invoice_number", "total"],
    },
}


def create_job(conn, doc_type: str, workspace: str = "default") -> str:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO jobs (kind, doc_type, workspace, status) "
            "VALUES ('document', %s, %s, 'EXTRACTING') RETURNING id::text",
            (doc_type, workspace),
        )
        return cur.fetchone()[0]


def _set_status(conn, job_id: str, status: str) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE jobs SET status = %s, updated_at = now() WHERE id = %s",
                    (status, job_id))


def ingest(conn, job_id: str, file_bytes: bytes, filename: str, doc_type: str,
           overrides: dict[str, float] | None = None) -> dict:
    """Extract a document, route every field, and record the whole thing.

    Raises dws.DWSUnavailable if no key is configured. That failure is recorded on
    the chain first, so an unrunnable job is auditable rather than merely absent.
    """
    sha = hashlib.sha256(file_bytes).hexdigest()
    audit.append_audit(conn, job_id, "upload", "AGENT", {
        "filename": filename, "bytes": len(file_bytes),
        "sha256": sha, "doc_type": doc_type,
    })

    schema = SCHEMAS.get(doc_type)
    if schema is None:
        audit.append_audit(conn, job_id, "extract.failed", "AGENT",
                           {"reason": "no_schema_for_doc_type", "doc_type": doc_type})
        _set_status(conn, job_id, "FAILED")
        raise ValueError(f"no extraction schema for doc_type {doc_type!r}")

    try:
        raw = dws.extract(file_bytes, filename, schema)
    except dws.DWSUnavailable as e:
        audit.append_audit(conn, job_id, "extract.unavailable", "AGENT",
                           {"reason": str(e)[:300], "endpoint": dws.EXTRACT_PATH})
        _set_status(conn, job_id, "FAILED")
        raise
    except dws.DWSError as e:
        audit.append_audit(conn, job_id, "extract.rejected", "AGENT",
                           {"reason": str(e)[:300], "endpoint": dws.EXTRACT_PATH})
        _set_status(conn, job_id, "FAILED")
        raise

    return _route_and_store(conn, job_id, dws.normalise(raw), overrides,
                            engine="nutrient-dws" + dws.EXTRACT_PATH)


def ingest_prepared(conn, job_id: str, fields: list[dict], engine: str,
                    overrides: dict[str, float] | None = None) -> dict:
    """Route an already-extracted field list.

    Exists so the gate can be exercised against a recorded extraction without a
    live key. `engine` is recorded verbatim in the audit chain, so a run that did
    not come from a live DWS call says so on its own record and cannot later be
    mistaken for one.
    """
    return _route_and_store(conn, job_id, fields, overrides, engine=engine)


def _route_and_store(conn, job_id: str, fields: list[dict],
                     overrides: dict[str, float] | None, engine: str) -> dict:
    audit.append_audit(conn, job_id, "extract", "AGENT", {
        "engine": engine,
        "fields": len(fields),
        "with_confidence": sum(1 for f in fields if f.get("confidence") is not None),
        "without_confidence": sum(1 for f in fields if f.get("confidence") is None),
    })

    routed = gate.route_all(fields, overrides)

    with conn.cursor() as cur:
        for f in routed:
            cur.execute(
                "INSERT INTO fields (job_id, name, value, confidence, recognition, "
                "source_bbox, decision, decision_reason, original_value) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (job_id, f["name"], f.get("value"), f.get("confidence"),
                 f.get("recognition"),
                 json.dumps(f["source_bbox"]) if f.get("source_bbox") else None,
                 f["decision"], f["reason"], f.get("value")),
            )

    summary = gate.summarise(routed)
    # The policy is recorded WITH the outcome, so every routing decision can be
    # re-derived later without this binary.
    audit.append_audit(conn, job_id, "route", "AGENT", {
        **summary, "policy_snapshot": gate.policy_snapshot(overrides),
    })

    status = "NEEDS_REVIEW" if summary["human"] else "APPROVED"
    _set_status(conn, job_id, status)
    audit.append_audit(conn, job_id, "status", "AGENT",
                       {"status": status,
                        "because": f"{summary['human']} field(s) require human review"
                                   if summary["human"] else
                                   "every field cleared its threshold"})

    return {"job_id": job_id, "status": status, "summary": summary, "fields": routed}


def job_fields(conn, job_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT name, value, confidence, recognition, decision, decision_reason, "
            "reviewed_by, reviewed_at::text FROM fields WHERE job_id = %s "
            "ORDER BY decision, name",
            (job_id,),
        )
        return [{"name": r[0], "value": r[1], "confidence": r[2], "recognition": r[3],
                 "decision": r[4], "reason": r[5], "reviewed_by": r[6],
                 "reviewed_at": r[7]} for r in cur.fetchall()]
