"""
Human review — Pattern 2. The gate that the pipeline cannot walk around.

A field routed to HUMAN is not merely flagged; the job is BLOCKED until a person
has ruled on every one. The spec's phrasing is the requirement: "nothing risky
proceeds without a human", so the resume path is written as a single guarded
transition rather than a status the caller can set.

Two distinctions the data model keeps that a looser one would lose:

  * `decision` is how the field was ROUTED. `reviewed_at` is whether a human has
    since RULED on it. Collapsing those two makes "a human approved this" and "a
    machine sent this to a human" indistinguishable in the audit trail — which is
    precisely the claim a regulator is checking.
  * `original_value` holds what the machine said. After a correction the record
    shows both, so a reviewer's edit is visible as an edit rather than silently
    replacing the extraction.
"""
from __future__ import annotations

from core.trust import audit

ACCEPT, CORRECT = "ACCEPT", "CORRECT"


class ReviewError(RuntimeError):
    """A review action that must not be applied."""


def pending(conn, job_id: str) -> list[dict]:
    """Fields still awaiting a human ruling, in the order a reviewer should see them.

    Worst first — lowest confidence at the top — so a reviewer working top-down
    spends attention where it is most likely to matter. Absent confidence sorts to
    the very top, because "no signal" deserves a look before "weak signal".
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id::text, name, value, confidence, recognition, decision_reason, "
            "       source_bbox "
            "FROM fields "
            "WHERE job_id = %s AND decision = 'HUMAN' AND reviewed_at IS NULL "
            "ORDER BY confidence ASC NULLS FIRST, name ASC",
            (job_id,),
        )
        return [{"id": r[0], "name": r[1], "value": r[2], "confidence": r[3],
                 "recognition": r[4], "reason": r[5], "source_bbox": r[6]}
                for r in cur.fetchall()]


def _job(conn, job_id: str) -> tuple[str, str]:
    with conn.cursor() as cur:
        cur.execute("SELECT status, kind FROM jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
    if row is None:
        raise ReviewError(f"no such job {job_id}")
    return row[0], row[1]


def decide(conn, job_id: str, field_id: str, action: str, reviewer: str,
           new_value: str | None = None) -> dict:
    """Record one human ruling on one field, then resume if that was the last.

    The field UPDATE is guarded on `reviewed_at IS NULL`, so two reviewers racing
    on the same field cannot both write a ruling — the second sees zero rows
    updated and is told the field was already decided. Without that guard the
    audit trail would carry two conflicting rulings and no way to tell which one
    the pipeline actually used.
    """
    if action not in (ACCEPT, CORRECT):
        raise ReviewError(f"unknown action {action!r} -- expected ACCEPT or CORRECT")
    if action == CORRECT and (new_value is None or new_value == ""):
        raise ReviewError("CORRECT requires a new value")

    status, _ = _job(conn, job_id)
    if status != "NEEDS_REVIEW":
        raise ReviewError(
            f"job is {status}, not NEEDS_REVIEW -- review is only accepted while the "
            f"pipeline is actually blocked on it"
        )

    with conn.cursor() as cur:
        cur.execute(
            "SELECT name, value, confidence, decision_reason FROM fields "
            "WHERE id = %s AND job_id = %s",
            (field_id, job_id),
        )
        row = cur.fetchone()
        if row is None:
            raise ReviewError(f"field {field_id} does not belong to job {job_id}")
        name, machine_value, confidence, routed_reason = row

        final_value = new_value if action == CORRECT else machine_value
        cur.execute(
            "UPDATE fields SET value = %s, reviewed_by = %s, reviewed_at = now() "
            "WHERE id = %s AND job_id = %s AND reviewed_at IS NULL",
            (final_value, reviewer, field_id, job_id),
        )
        if cur.rowcount == 0:
            raise ReviewError(f"field {name!r} has already been reviewed")

    # The audit entry names the person, the field, and BOTH values. "who changed
    # what and when" has to be answerable from this row alone.
    audit.append_audit(conn, job_id, "review", "HUMAN", {
        "field": name,
        "action": action,
        "reviewer": reviewer,
        "routed_because": routed_reason,
        "machine_confidence": confidence,
        "machine_value": machine_value,
        "final_value": final_value,
        "changed": action == CORRECT and final_value != machine_value,
    })

    remaining = len(pending(conn, job_id))
    resumed = False
    if remaining == 0:
        resumed = _resume(conn, job_id, reviewer)

    return {"field": name, "action": action, "final_value": final_value,
            "remaining": remaining, "resumed": resumed}


def _resume(conn, job_id: str, reviewer: str) -> bool:
    """Move NEEDS_REVIEW -> APPROVED, but only if nothing is outstanding.

    The status change is guarded in SQL on both the current status AND the absence
    of unreviewed fields, so this cannot advance a job even if called directly.
    The gate is in the database, not in the caller's good manners.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET status = 'APPROVED', updated_at = now() "
            "WHERE id = %s AND status = 'NEEDS_REVIEW' "
            "  AND NOT EXISTS (SELECT 1 FROM fields "
            "                  WHERE job_id = %s AND decision = 'HUMAN' "
            "                    AND reviewed_at IS NULL)",
            (job_id, job_id),
        )
        if cur.rowcount == 0:
            return False

    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FILTER (WHERE decision = 'AUTO'), "
            "       count(*) FILTER (WHERE decision = 'HUMAN'), "
            "       count(*) FILTER (WHERE value IS DISTINCT FROM original_value) "
            "FROM fields WHERE job_id = %s",
            (job_id,),
        )
        auto, human, corrected = cur.fetchone()

    audit.append_audit(conn, job_id, "status", "AGENT", {
        "status": "APPROVED",
        "because": "every field routed to a human has been ruled on",
        "auto_accepted": auto, "human_reviewed": human, "human_corrected": corrected,
        "last_reviewer": reviewer,
    })
    return True


def progress(conn, job_id: str) -> dict:
    """What a reviewer needs to see: how much is left, and what has been ruled on."""
    status, _ = _job(conn, job_id)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FILTER (WHERE decision = 'HUMAN'), "
            "       count(*) FILTER (WHERE decision = 'HUMAN' AND reviewed_at IS NOT NULL), "
            "       count(*) FILTER (WHERE decision = 'AUTO'), "
            "       count(*) FILTER (WHERE value IS DISTINCT FROM original_value) "
            "FROM fields WHERE job_id = %s",
            (job_id,),
        )
        h, done, auto, corrected = cur.fetchone()
    return {"status": status, "auto": auto, "needs_review": h, "reviewed": done,
            "remaining": h - done, "corrected": corrected}
