"""
Forget: verifiable, atomic erasure of an entity from memory.

The pre-deletion moment is anchored with cluster_logical_timestamp() in a SEPARATE statement
BEFORE the deleting transaction opens. This ordering is deliberate and load-bearing: anchoring
inside the transaction would make t_before equal the commit timestamp, so an AS OF SYSTEM TIME
read at that value would see the POST-delete state and the proof would return nothing. Anchoring
first means the deletes commit strictly later, so AS OF SYSTEM TIME t_before reconstructs exactly
what existed before erasure.

  1. (before the txn) anchor t_before = cluster_logical_timestamp() — the AS OF SYSTEM TIME proof.

Before any of that, erasure can be REFUSED. GDPR Art. 17(3) carves the right to erasure out
where processing is necessary to comply with a legal obligation, or to establish, exercise or
defend a legal claim. A subject under legal hold raises LegalHold and nothing is touched — the
check runs before the anchor is taken, so a refusal leaves no half-started erasure behind. Most
erasure systems implement only the right; implementing the exception is what makes this one
usable in a regulated setting.

The erasure itself is then ONE serializable CockroachDB transaction (steps 2–5 commit together, so
memory is never left in a half-erased state):
  2. hard-delete the entity's SUBJECT-EXCLUSIVE knowledge — documents, graph nodes, and every
     edge touching them, EXCEPT anything marked authoritative: a statute or regulator charter
     holds no personal data, so erasing a person must destroy their evidence and leave the law
     standing. The retained count is signed into the certificate,
  3. INVALIDATE (not delete) SHARED nodes — entities that also belong to a surviving subject stay,
     with the erased subject removed from their provenance, so no other subject's memory is corrupted,
  4. crypto-shred the subject's data key, making any residual ciphertext (MVCC history, backups, S3)
     cryptographically unrecoverable,
  5. record a measured, tamper-evident erasure event.
"""
from __future__ import annotations

import os
import re

from db import store
from core.trust import pipeline_job as _job


class LegalHold(Exception):
    """Raised when erasure is refused because a retention obligation is in force.

    GDPR Art. 17(3) carves the right to erasure out where processing is necessary to
    comply with a legal obligation (b) or to establish, exercise or defend a legal
    claim (e). Refusing loudly is the point: a system that quietly kept the data would
    be indistinguishable from one that failed to delete it.
    """

    def __init__(self, subject: str, reason: str | None, until):
        self.subject, self.reason, self.until = subject, reason, until
        super().__init__(f"erasure refused — {subject} is under legal hold")


def hold_status(conn, workspace: str, subject: str) -> dict | None:
    """Return the ACTIVE hold on a subject, or None. A hold with a past `hold_until`
    has lapsed and no longer blocks erasure."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT hold_reason, hold_until::string, held_at::string FROM subject_keys "
            "WHERE workspace = %s AND subject = %s "
            "AND held_at IS NOT NULL AND (hold_until IS NULL OR hold_until > now())",
            (workspace, subject),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {"reason": row[0], "until": row[1], "since": row[2]}


def set_hold(subject: str, reason: str, until: str | None = None,
             workspace: str = "default") -> dict:
    """Place a subject under legal hold. Erasure is refused until it is released or lapses."""
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO subject_keys (workspace, subject, hold_reason, hold_until, held_at) "
            "VALUES (%s, %s, %s, %s::TIMESTAMPTZ, now()) "
            "ON CONFLICT (workspace, subject) DO UPDATE SET "
            "  hold_reason = excluded.hold_reason, hold_until = excluded.hold_until, held_at = now()",
            (workspace, subject, reason, until),
        )
        cur.execute(
            "INSERT INTO timeline (workspace, kind, subject, detail) VALUES (%s, 'hold', %s, %s)",
            (workspace, subject,
             f"legal hold placed on {subject}: {reason}" + (f" (until {until})" if until else "")),
        )
        hj = _job.open_job(conn, kind="erasure", subject=subject, workspace=workspace,
                           status="NEEDS_REVIEW")
        _job.record(conn, hj, "hold.placed", "HUMAN",
                    {"subject": subject, "reason": reason, "until": until})
    return {"subject": subject, "workspace": workspace, "reason": reason, "until": until, "held": True}


def release_hold(subject: str, workspace: str = "default") -> dict:
    """Lift a legal hold. Erasure becomes permitted again; the release is recorded."""
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE subject_keys SET hold_reason = NULL, hold_until = NULL, held_at = NULL "
            "WHERE workspace = %s AND subject = %s",
            (workspace, subject),
        )
        n = cur.rowcount
        cur.execute(
            "INSERT INTO timeline (workspace, kind, subject, detail) VALUES (%s, 'hold', %s, %s)",
            (workspace, subject, f"legal hold released on {subject} — erasure permitted"),
        )
        rj = _job.open_job(conn, kind="erasure", subject=subject, workspace=workspace,
                           status="APPROVED")
        _job.record(conn, rj, "hold.released", "HUMAN", {"subject": subject})
    return {"subject": subject, "workspace": workspace, "released": n > 0}


def forget(subject: str, workspace: str = "default") -> dict:
    """Erase `subject` from a workspace's memory. Returns a measured receipt.

    Raises LegalHold if a retention obligation is in force — checked BEFORE the
    anchor is taken, so a refused erasure leaves no trace of a half-started one.
    """
    with store.connect() as conn:
        # Same spine as the document pipeline: one chain, one vocabulary. Opened
        # before the hold check so that a REFUSAL is recorded too -- a refused
        # erasure is exactly the event a regulator wants evidence of, and a system
        # that only logs successes cannot prove it ever said no.
        job_id = _job.open_job(conn, kind="erasure", subject=subject,
                               workspace=workspace, status="EXTRACTING")
        _job.record(conn, job_id, "erasure.requested", "AGENT",
                    {"subject": subject, "workspace": workspace})

        held = hold_status(conn, workspace, subject)
        if held:
            _job.record(conn, job_id, "erasure.refused", "AGENT", {
                "subject": subject, "reason": held["reason"], "until": held["until"],
                "basis": "GDPR Art. 17(3) -- retention obligation in force",
            })
            _job.set_status(conn, job_id, "REFUSED")
            raise LegalHold(subject, held["reason"], held["until"])

        receipt = {"docs": 0, "nodes": 0, "edges": 0, "invalidated": 0, "authoritative_retained": 0}

        # Anchor the pre-deletion moment BEFORE the deleting transaction opens (autocommit), so the
        # deletes commit at a STRICTLY LATER timestamp and an AS OF SYSTEM TIME t_before read sees the
        # pre-delete state. Anchoring INSIDE the txn makes cluster_logical_timestamp() equal the commit
        # timestamp, so AOST t_before reads the POST-delete state and the proof returns nothing —
        # empirically verified (0 rows vs 1). This ordering is what makes the proof real.
        with conn.cursor() as cur:
            cur.execute("SELECT cluster_logical_timestamp()::string")
            t_before = store.scalar(cur)

        with conn.transaction():
            with conn.cursor() as cur:
                # subject-exclusive nodes: subject is present AND is the ONLY distinct subject
                # Authoritative material — statutes, regulator charters — carries no
                # personal data. It is excluded from the cascade so that erasing a person
                # destroys their evidence and LEAVES THE LAW STANDING. This is the
                # difference between a thorough delete and a lawful one.
                cur.execute(
                    """
                    SELECT id FROM nodes
                    WHERE workspace = %s
                      AND %s::STRING = ANY(subjects)
                      AND array_length(array_remove(subjects, %s::STRING), 1) IS NULL
                      AND deleted_at IS NULL
                      AND source_kind <> 'authoritative'
                    """,
                    (workspace, subject, subject),
                )
                exclusive = [r[0] for r in cur.fetchall()]

                # Count what the exclusion spared, so the certificate can attest to it.
                cur.execute(
                    """
                    SELECT count(*) FROM nodes
                    WHERE workspace = %s
                      AND %s::STRING = ANY(subjects)
                      AND deleted_at IS NULL
                      AND source_kind = 'authoritative'
                    """,
                    (workspace, subject),
                )
                receipt["authoritative_retained"] = store.scalar(cur)

                if exclusive:
                    cur.execute(
                        "DELETE FROM edges WHERE workspace = %s AND (source_id = ANY(%s) OR target_id = ANY(%s))",
                        (workspace, exclusive, exclusive),
                    )
                    receipt["edges"] = cur.rowcount
                    cur.execute("DELETE FROM nodes WHERE id = ANY(%s)", (exclusive,))
                    receipt["nodes"] = cur.rowcount

                # shared nodes: keep for surviving subjects, remove the erased subject from provenance
                cur.execute(
                    """
                    UPDATE nodes
                    SET subjects = array_remove(subjects, %s::STRING)
                    WHERE workspace = %s
                      AND %s::STRING = ANY(subjects)
                      AND array_length(array_remove(subjects, %s::STRING), 1) >= 1
                      AND deleted_at IS NULL
                    """,
                    (subject, workspace, subject, subject),
                )
                receipt["invalidated"] = cur.rowcount

                # The subject's documents — ALL of them, including any marked authoritative.
                #
                # The asymmetry with nodes above is deliberate. Node text is stored in
                # plaintext, so an authoritative node genuinely survives and stays readable.
                # Document content is sealed under this subject's DEK, and the next step
                # destroys that key: a retained authoritative document would be a row nobody
                # can ever decrypt, inflating the retention count with dead bytes and making
                # the certificate claim something false. Deleting it is the honest outcome.
                #
                # In practice authoritative material belongs to its own subject (the statute,
                # not the person), so a person's erasure never reaches it. This branch only
                # fires when a regulation was mis-ingested under someone's name.
                cur.execute("DELETE FROM documents WHERE workspace = %s AND subject = %s",
                            (workspace, subject))
                receipt["docs"] = cur.rowcount

                # crypto-shred: destroy the subject's data key
                store.crypto_shred(conn, workspace, subject)

                # measured, tamper-evident erasure event. A random per-event salt makes the
                # certificate's subject hash non-reversible: the salt is stored here (operator side)
                # but is NEVER written into the portable/S3 certificate, so a leaked cert cannot be
                # brute-forced back to the subject — even for low-entropy names.
                salt = os.urandom(16)
                cur.execute(
                    """
                    INSERT INTO erasure_events
                      (workspace, subject, subject_salt, t_before, docs_removed, nodes_removed,
                       edges_removed, nodes_invalidated, authoritative_retained)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (workspace, subject, salt, t_before, receipt["docs"], receipt["nodes"],
                     receipt["edges"], receipt["invalidated"], receipt["authoritative_retained"]),
                )
                event_id = store.scalar(cur)

                cur.execute(
                    "INSERT INTO timeline (workspace, kind, subject, detail) VALUES (%s, 'forget', %s, %s)",
                    (workspace, subject,
                     f"erased {subject}: {receipt['nodes']} nodes, {receipt['edges']} edges, "
                     f"{receipt['docs']} docs deleted; {receipt['invalidated']} shared nodes retained; "
                     f"{receipt['authoritative_retained']} authoritative sources retained; "
                     f"key crypto-shredded"),
                )

        # After the transaction commits, so the chain records what actually landed
        # rather than what was attempted.
        _job.record(conn, job_id, "erasure.completed", "AGENT", {
            "subject": subject, "event_id": str(event_id), "t_before": t_before,
            **receipt,
        })
        _job.set_status(conn, job_id, "SIGNED")
        head, length = _job.chain_head(conn, job_id)

    return {"subject": subject, "workspace": workspace, "t_before": t_before,
            "event_id": str(event_id), "salt": salt.hex(),
            "job_id": job_id, "chain_head": head, "chain_length": length, **receipt}


def prior_state(subject: str, t_before: str, workspace: str = "default") -> list[dict]:
    """Proof-of-prior-existence: reconstruct what the graph knew about `subject` just before erasure,
    via AS OF SYSTEM TIME. Returns the nodes that existed then. (AOST timestamp must be a literal.)"""
    if not re.fullmatch(r"-?\d+(\.\d+)?", str(t_before)):
        raise ValueError("invalid AS OF SYSTEM TIME anchor")
    with store.connect() as conn, conn.cursor() as cur:
        # t_before is regex-validated above as a bare number, not passed through as
        # arbitrary input — CockroachDB requires AS OF SYSTEM TIME to be a literal, so
        # it cannot be a bind parameter. psycopg's stub wants a compile-time
        # LiteralString here, which no runtime-checked value can ever satisfy.
        cur.execute(
            f"SELECT name, type, description FROM nodes AS OF SYSTEM TIME {t_before} "
            "WHERE workspace = %s AND %s::STRING = ANY(subjects)",  # pyright: ignore[reportArgumentType]
            (workspace, subject),
        )
        return [{"name": r[0], "type": r[1], "description": r[2]} for r in cur.fetchall()]


def verify_gone(subject: str, workspace: str = "default") -> dict:
    """Proof-of-absence: after erasure, the subject's exclusive knowledge and key are gone."""
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM nodes WHERE workspace = %s AND %s::STRING = ANY(subjects) "
            "AND deleted_at IS NULL",
            (workspace, subject),
        )
        live_nodes = store.scalar(cur)
        cur.execute("SELECT count(*) FROM documents WHERE workspace = %s AND subject = %s", (workspace, subject))
        live_docs = store.scalar(cur)
        cur.execute(
            "SELECT wrapped_dek IS NULL, destroyed_at IS NOT NULL FROM subject_keys "
            "WHERE workspace = %s AND subject = %s",
            (workspace, subject),
        )
        row = cur.fetchone()
        key_shredded = bool(row and row[0] and row[1])

        # live vector re-search: embed the subject and confirm none of the nearest surviving nodes
        # still carry it — proof-of-absence at the VECTOR layer, not just relational counts.
        try:
            from llm import client
            qv = store.to_vector(client.embed(subject))
            cur.execute(
                "SELECT count(*) FROM ("
                "  SELECT subjects FROM nodes "
                "  WHERE workspace = %s AND deleted_at IS NULL AND embedding IS NOT NULL "
                "  ORDER BY embedding <=> %s LIMIT 20"
                ") t WHERE %s::STRING = ANY(subjects)",
                (workspace, qv, subject),
            )
            vector_hits = store.scalar(cur)
        except Exception:
            vector_hits = None
    return {"live_exclusive_nodes": live_nodes, "live_docs": live_docs,
            "vector_hits": vector_hits, "key_shredded": key_shredded}
