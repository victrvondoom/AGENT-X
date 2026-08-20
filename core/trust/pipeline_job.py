"""
Job creation for any pipeline — the join between a pipeline and the shared spine.

Both pipelines get a `jobs` row and write to the same hash chain, so "what happened
to this data" has one answer regardless of which pipeline did it. Without this the
erasure pipeline would keep its own private trail and the shared-spine claim would
be half true — the document flow auditable, the erasure flow merely logged.

Deliberately tiny, and deliberately tolerant. Erasure worked before the spine
existed and must keep working if the spine is unavailable: `open_job` returns None
rather than raising, and `record` is a no-op on a None job. A trust system that can
take down the operation it is observing is a worse trade than a trust system with a
gap, and the gap is visible — a run with no chain has no certificate binding either.
"""
from __future__ import annotations

from . import audit


def open_job(conn, *, kind: str, subject: str | None = None,
             workspace: str = "default", doc_type: str | None = None,
             status: str = "EXTRACTING") -> str | None:
    """Create a job row for a pipeline run. None if the spine is not migrated."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO jobs (kind, doc_type, subject, workspace, status) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id::text",
                (kind, doc_type, subject, workspace, status),
            )
            return cur.fetchone()[0]
    except Exception:
        # jobs table absent (spine migration not applied) -- the pipeline proceeds
        # unaudited rather than failing the user's operation.
        return None


def record(conn, job_id: str | None, step: str, actor: str, detail: dict) -> dict | None:
    """Append to the shared chain, if this run has one."""
    if job_id is None:
        return None
    try:
        return audit.append_audit(conn, job_id, step, actor, detail)
    except Exception:
        return None


def set_status(conn, job_id: str | None, status: str) -> None:
    if job_id is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE jobs SET status = %s, updated_at = now() WHERE id = %s",
                        (status, job_id))
    except Exception:
        pass


def chain_head(conn, job_id: str | None) -> tuple[str | None, int | None]:
    """(head_hash, length) for binding into a certificate."""
    if job_id is None:
        return None, None
    try:
        v = audit.verify_chain(conn, job_id)
        return v.get("head"), v.get("rows")
    except Exception:
        return None, None
