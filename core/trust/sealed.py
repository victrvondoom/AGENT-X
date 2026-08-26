"""
Crypto-shreddable audit detail — where the two pipelines actually meet.

An audit chain must be immutable or it proves nothing. Erasure must destroy personal
data. The chain contains personal data. Deleting a hash-chained row destroys every
hash after it, so the two obligations look mutually exclusive, and most systems
quietly pick one.

They are not exclusive. Seal the sensitive detail under the subject's key and hash
the CIPHERTEXT. Erasure destroys the key, and afterwards:

  * every hash still matches, because the ciphertext was never touched -- the chain
    still proves the event happened, when, and in what order;
  * the content is unrecoverable, because the key is gone.

Proof kept, personal data gone. That is what an immutable ledger normally cannot
offer, and it is the erasure pipeline lending its crypto-shred to the document
pipeline's audit trail.

A deliberate asymmetry: only the VALUES are sealed. Step names, actors, timestamps
and sequence stay in clear text, because after an erasure a regulator still has to
be able to read "a human reviewed a field on this date and the pipeline then
refused to proceed". Sealing the shape of the record as well as its contents would
destroy the audit trail in the name of protecting it.
"""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from . import audit

TOMBSTONE = "<crypto-shredded: the key for this subject was destroyed>"

# Kept in clear text so the trail remains readable after a shred. Anything not in
# this set is treated as potentially personal and goes inside the envelope.
CLEAR_KEYS = {"step", "actor", "status", "result", "field", "action", "engine",
              "endpoint", "reason", "because", "checked", "policy_snapshot",
              "auto", "human", "total", "corrected", "algorithm", "signed"}


def split_detail(detail: dict) -> tuple[dict, dict]:
    """Partition a detail dict into (clear, sensitive)."""
    clear, sensitive = {}, {}
    for k, v in detail.items():
        (clear if k in CLEAR_KEYS else sensitive)[k] = v
    return clear, sensitive


def append_sealed(conn, job_id: str, step: str, actor: str, detail: dict,
                  *, subject: str, workspace: str = "default") -> dict:
    """Append a chain row whose sensitive half is sealed under the subject's key.

    The hash covers the envelope exactly as written, ciphertext included, so the
    chain verifies identically before and after the key is destroyed. Nothing about
    verification changes -- which is the point: a verifier does not need to know
    whether a row has been shredded.
    """
    from db import store

    if actor not in ("AGENT", "HUMAN", "SYSTEM"):
        raise ValueError(f"unknown actor {actor!r}")

    clear, sensitive = split_detail(detail)
    envelope: dict[str, Any] = {"_clear": clear, "_sealed": None}

    if sensitive:
        try:
            blob = store.encrypt_for(conn, workspace, subject,
                                     json.dumps(sensitive, sort_keys=True,
                                                separators=(",", ":"), default=str))
            envelope["_sealed"] = base64.b64encode(bytes(blob)).decode()
        except store.KeyDestroyed:
            # The subject was already erased. Recording the values now would
            # re-create data we are legally required not to hold.
            envelope["_sealed"] = None
            envelope["_clear"]["sealed_omitted"] = "subject key already destroyed"

    with conn.cursor() as cur:
        cur.execute("SELECT id FROM jobs WHERE id = %s FOR UPDATE", (job_id,))
        if cur.fetchone() is None:
            raise ValueError(f"no such job {job_id}")

        last_seq, prev = audit.head(conn, job_id)
        seq = last_seq + 1
        blob_text = audit.canonical(envelope)
        content_hash = hashlib.sha256((prev + blob_text).encode("utf-8")).hexdigest()

        cur.execute(
            "INSERT INTO audit_log (job_id, seq, step, actor, detail, detail_canonical, "
            "prev_hash, content_hash, sealed, seal_subject, seal_workspace) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,TRUE,%s,%s)",
            (job_id, seq, step, actor, blob_text, blob_text, prev, content_hash,
             subject, workspace),
        )
    return {"seq": seq, "prev_hash": prev, "content_hash": content_hash,
            "sealed": bool(envelope["_sealed"])}


def open_detail(conn, row: dict) -> dict:
    """Return a chain row's detail, unsealing it if the key still exists.

    After a shred this returns the clear half plus a tombstone. It does NOT raise:
    a shredded row is a normal, expected state of a healthy chain, and a reader that
    crashes on one would make the trail unreadable precisely when it matters.
    """
    from db import store

    detail = row.get("detail")
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except ValueError:
            return {"_unreadable": True}
    if not isinstance(detail, dict) or "_clear" not in detail:
        return detail if isinstance(detail, dict) else {}

    out = dict(detail.get("_clear") or {})
    sealed_b64 = detail.get("_sealed")
    if not sealed_b64:
        return out

    subject, workspace = row.get("seal_subject"), row.get("seal_workspace") or "default"
    if subject is None:
        out["_unreadable"] = True
        return out
    try:
        plain = store.decrypt_for(conn, workspace, subject,
                                  base64.b64decode(sealed_b64))
    except Exception:
        plain = None
    if plain is None:
        out["_shredded"] = TOMBSTONE
        return out
    try:
        out.update(json.loads(plain))
    except ValueError:
        out["_unreadable"] = True
    return out


def readable_chain(conn, job_id: str) -> list[dict]:
    """The chain with sealed rows opened where possible — for display and export."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT seq, step, actor, detail, prev_hash, content_hash, ts::text, "
            "       sealed, seal_subject, seal_workspace "
            "FROM audit_log WHERE job_id = %s ORDER BY seq ASC",
            (job_id,),
        )
        rows = cur.fetchall()
    out = []
    for r in rows:
        row = {"seq": r[0], "step": r[1], "actor": r[2], "detail": r[3],
               "prev_hash": r[4], "content_hash": r[5], "ts": r[6],
               "sealed": r[7], "seal_subject": r[8], "seal_workspace": r[9]}
        row["detail"] = open_detail(conn, row) if r[7] else r[3]
        row["shredded"] = isinstance(row["detail"], dict) and "_shredded" in row["detail"]
        out.append(row)
    return out


def redact_subject(conn, workspace: str, subject: str, erasure_job: str | None) -> dict:
    """Redact a subject's values from document jobs, keeping the compliance record.

    A compliance job is itself a legal record: it proves the document was handled
    correctly. Deleting it to satisfy an erasure request would destroy the evidence
    of lawful handling -- so the personal VALUES go and the record stays, with the
    redaction written onto the job's own chain rather than performed silently.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id::text FROM jobs WHERE workspace = %s AND subject = %s "
            "AND kind = 'document'",
            (workspace, subject),
        )
        job_ids = [r[0] for r in cur.fetchall()]

    redacted_fields = 0
    for jid in job_ids:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE fields SET value = NULL, original_value = NULL, "
                "redacted_at = now(), redaction_job = %s "
                "WHERE job_id = %s AND redacted_at IS NULL",
                (erasure_job, jid),
            )
            redacted_fields += cur.rowcount
        # append, never edit: history stays intact and the redaction is itself audited
        audit.append_audit(conn, jid, "redacted", "SYSTEM", {
            "reason": "subject erased under GDPR Art. 17",
            "erasure_job": erasure_job,
            "note": "field values removed; the compliance record and its chain are "
                    "retained as evidence of lawful handling",
        })

    return {"document_jobs": len(job_ids), "fields_redacted": redacted_fields,
            "job_ids": job_ids}
