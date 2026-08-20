"""
Generate, sign, and SELF-VERIFY — Pattern 3.

The self-verifying loop is the step that stops this being a document generator with
good intentions. After the artefact exists, the agent RE-READS it and asserts every
field matches what a human approved. A mismatch fails the job loudly and names the
field; it never ships quietly.

The comparison is deliberately strict. Normalising away case or punctuation before
comparing would make the check pass on exactly the corruptions it exists to catch —
"12,480.00" and "1,248.00" differ by punctuation. Only leading/trailing whitespace
is ignored, because that is a rendering artefact rather than a value change.
"""
from __future__ import annotations

from core.trust import audit
from . import dws, generate, sign


class SelfVerifyFailed(RuntimeError):
    def __init__(self, mismatches: list[dict]):
        self.mismatches = mismatches
        super().__init__(f"self-verify failed on {len(mismatches)} field(s)")


def approved_fields(conn, job_id: str) -> dict[str, str]:
    """The values a human signed off on — the ground truth for self-verification."""
    with conn.cursor() as cur:
        cur.execute("SELECT name, value FROM fields WHERE job_id = %s ORDER BY name",
                    (job_id,))
        return {r[0]: (r[1] or "") for r in cur.fetchall()}


def compare(approved: dict[str, str], readback: dict[str, str]) -> list[dict]:
    """Every way the artefact can disagree with what was approved."""
    out = []
    for name, want in approved.items():
        if name not in readback:
            out.append({"field": name, "problem": "missing_from_document",
                        "approved": want, "found": None})
        elif readback[name].strip() != want.strip():
            out.append({"field": name, "problem": "value_mismatch",
                        "approved": want, "found": readback[name]})
    for name in readback:
        if name not in approved:
            out.append({"field": name, "problem": "not_in_approved_set",
                        "approved": None, "found": readback[name]})
    return out


def finalize(conn, job_id: str, title: str = "Certificate of Compliance",
             corrupt: tuple[str, str] | None = None) -> dict:
    """Generate -> sign -> re-read -> compare. Sets SIGNED or FAILED.

    `corrupt` is a deliberate-tamper hook for the Phase 4 demo: it alters the
    generated artefact AFTER approval and BEFORE the re-read, which is exactly the
    situation the loop exists to catch. It is a test affordance and is recorded on
    the audit chain when used, so a corrupted run can never be mistaken for a clean
    one.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
    if row is None:
        raise ValueError(f"no such job {job_id}")
    if row[0] != "APPROVED":
        # Refuse rather than generate: an artefact built from unapproved fields is
        # the exact failure the human gate exists to prevent.
        raise ValueError(f"job is {row[0]}, not APPROVED -- nothing may be generated "
                         f"until every field has been ruled on")

    approved = approved_fields(conn, job_id)

    # ── generate ──────────────────────────────────────────────────────────
    to_render = dict(approved)
    if corrupt:
        to_render[corrupt[0]] = corrupt[1]
    pdf = generate.build_pdf(title, to_render)
    if corrupt:
        audit.append_audit(conn, job_id, "generate.corrupted", "SYSTEM", {
            "field": corrupt[0], "approved": approved.get(corrupt[0]),
            "written": corrupt[1],
            "note": "deliberate corruption injected to exercise the self-verify loop",
        })

    doc_hash = generate.sha256(pdf)
    audit.append_audit(conn, job_id, "generate", "AGENT", {
        "engine": "local-pdf" if not dws.configured() else "local-pdf",
        "bytes": len(pdf), "sha256": doc_hash, "fields": len(to_render),
    })

    # ── sign ──────────────────────────────────────────────────────────────
    doc_signed = False
    try:
        pdf = sign.sign_document(pdf)
        doc_hash = generate.sha256(pdf)
        doc_signed = True
        audit.append_audit(conn, job_id, "sign.document", "AGENT",
                           {"endpoint": dws.SIGN_PATH, "sha256": doc_hash})
    except (dws.DWSUnavailable, dws.DWSError) as e:
        audit.append_audit(conn, job_id, "sign.document.unavailable", "AGENT", {
            "endpoint": dws.SIGN_PATH, "reason": str(e)[:300],
            "consequence": "the PDF is NOT a signed PDF; only a detached signature "
                           "over its hash is produced",
        })

    detached = sign.sign_detached(doc_hash.encode())
    audit.append_audit(conn, job_id, "sign.detached", "AGENT", {
        "algorithm": detached["algorithm"], "signed": detached["signed"],
        "over": "sha256(document)", "document_sha256": doc_hash,
        **({"reason": detached["reason"]} if not detached["signed"] else {}),
    })

    # ── self-verify: re-read the artefact, do not trust memory ────────────
    readback = generate.read_pdf_fields(pdf)
    mismatches = compare(approved, readback)

    if mismatches:
        audit.append_audit(conn, job_id, "self-verify", "AGENT", {
            "result": "FAIL", "mismatches": mismatches,
            "checked": len(approved),
            "note": "the generated document does not match the approved values; "
                    "the job is failed rather than shipped",
        })
        with conn.cursor() as cur:
            cur.execute("UPDATE jobs SET status='FAILED', updated_at=now() WHERE id=%s",
                        (job_id,))
        audit.append_audit(conn, job_id, "status", "AGENT",
                           {"status": "FAILED",
                            "because": f"self-verify found {len(mismatches)} mismatch(es)"})
        raise SelfVerifyFailed(mismatches)

    audit.append_audit(conn, job_id, "self-verify", "AGENT", {
        "result": "PASS", "checked": len(approved),
        "method": "re-read the generated document and compared every approved field",
    })
    with conn.cursor() as cur:
        cur.execute("UPDATE jobs SET status='SIGNED', updated_at=now() WHERE id=%s",
                    (job_id,))
    audit.append_audit(conn, job_id, "status", "AGENT",
                       {"status": "SIGNED", "because": "self-verify passed"})

    return {"job_id": job_id, "status": "SIGNED", "document_sha256": doc_hash,
            "bytes": len(pdf), "document_signature_embedded": doc_signed,
            "detached_signature": detached, "self_verify": "PASS",
            "fields_checked": len(approved), "pdf": pdf}
