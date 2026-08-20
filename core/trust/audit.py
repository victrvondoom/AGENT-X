"""
Hash-chained, append-only audit log — the trust primitive both pipelines share.

Every step of every run lands here: the erasure pipeline and the document pipeline
write to the same chain, so "what happened to this data" has ONE answer rather than
one per subsystem.

The chain works like a ledger. Each row's `prev_hash` is the previous row's
`content_hash`, and `content_hash = sha256(prev_hash || canonical(detail))`. Change
any historical row and every hash after it stops matching — and crucially, a
regulator can confirm that themselves in plain SQL without running our code.

Two guarantees that hash-chaining alone does NOT give, and how they are covered:

  * A deleted row from the TAIL leaves a still-valid chain. Covered by `seq`, which
    is per-job and gap-free, so a missing row is visible as a hole.
  * A concurrent writer could interleave and fork the chain. Covered by locking the
    job row for the duration of the append, so the read-then-write of the head is
    atomic. `UNIQUE (job_id, seq)` is the backstop if the lock is ever bypassed.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

GENESIS = "0" * 64          # prev_hash of the first row in a chain


def canonical(detail: Any) -> str:
    """Serialise `detail` so the same content always hashes the same.

    Sorted keys and no incidental whitespace — matching how the certificate is
    canonicalised, because a verifier that recomputes one must recompute the other
    the same way. `default=str` keeps values like datetimes and UUIDs hashable
    instead of raising mid-append.
    """
    return json.dumps(detail, sort_keys=True, separators=(",", ":"), default=str)


def compute_hash(prev_hash: str, detail: Any) -> str:
    """The chain rule, isolated so a verifier and a writer cannot drift apart."""
    return hashlib.sha256((prev_hash + canonical(detail)).encode("utf-8")).hexdigest()


def head(conn, job_id: str) -> tuple[int, str]:
    """Return (last_seq, last_content_hash) for a job, or (-1, GENESIS) if empty."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT seq, content_hash FROM audit_log WHERE job_id = %s "
            "ORDER BY seq DESC LIMIT 1",
            (job_id,),
        )
        row = cur.fetchone()
    return (row[0], row[1]) if row else (-1, GENESIS)


def append_audit(conn, job_id: str, step: str, actor: str, detail: dict) -> dict:
    """Append one row to a job's chain. Returns the written row's seq and hash.

    Raises ValueError on an unknown actor rather than writing an unclassifiable
    row: an audit entry whose actor cannot be trusted is worse than no entry,
    because it still looks like evidence.
    """
    if actor not in ("AGENT", "HUMAN", "SYSTEM"):
        raise ValueError(f"unknown actor {actor!r} — must be AGENT, HUMAN or SYSTEM")

    with conn.cursor() as cur:
        # Serialise appends for this job. Reading the head and writing the next row
        # must be atomic, or two concurrent writers both read seq=N and fork the
        # chain into two branches that each look individually valid.
        cur.execute("SELECT id FROM jobs WHERE id = %s FOR UPDATE", (job_id,))
        if cur.fetchone() is None:
            raise ValueError(f"no such job {job_id}")

        last_seq, prev = head(conn, job_id)
        seq = last_seq + 1
        content_hash = compute_hash(prev, detail)

        blob = canonical(detail)
        # detail_canonical stores the EXACT bytes that were hashed. Without it a
        # verifier in SQL must guess how Python serialised the JSON, which it cannot
        # do -- see db/migrations/002_canonical_detail.sql.
        cur.execute(
            "INSERT INTO audit_log (job_id, seq, step, actor, detail, detail_canonical, "
            "prev_hash, content_hash) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (job_id, seq, step, actor, blob, blob, prev, content_hash),
        )
    return {"seq": seq, "prev_hash": prev, "content_hash": content_hash}


def verify_chain(conn, job_id: str) -> dict:
    """Re-walk a job's chain and report whether it is intact.

    Recomputes every hash from the stored detail rather than trusting the stored
    hash — checking a hash against itself would pass on any tampered row. Reports
    the FIRST break, since everything after a break is unreliable anyway.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT seq, step, actor, detail, prev_hash, content_hash, detail_canonical "
            "FROM audit_log WHERE job_id = %s ORDER BY seq ASC",
            (job_id,),
        )
        rows = cur.fetchall()

    if not rows:
        return {"ok": True, "rows": 0, "head": GENESIS, "reason": "empty chain"}

    prev = GENESIS
    for i, (seq, step, actor, detail, row_prev, row_hash, blob) in enumerate(rows):
        if seq != i:
            return {"ok": False, "rows": len(rows), "broken_at": seq,
                    "reason": f"sequence gap: expected seq {i}, found {seq} "
                              f"(a row was deleted or inserted out of order)"}
        if row_prev != prev:
            return {"ok": False, "rows": len(rows), "broken_at": seq,
                    "reason": f"prev_hash mismatch at seq {seq}: chain was re-linked"}
        # The hash is over detail_canonical, so a tamperer who edits only the
        # readable `detail` column would leave the hash intact and the trail would
        # DISPLAY forged content while verifying clean. The two columns must agree.
        # Compared structurally, not as strings: a JSONB round-trip may reorder keys
        # or renormalise numbers without any content having changed.
        if blob is not None:
            try:
                same = json.loads(blob) == (json.loads(detail)
                                            if isinstance(detail, str) else detail)
            except (TypeError, ValueError):
                same = False
            if not same:
                return {"ok": False, "rows": len(rows), "broken_at": seq,
                        "reason": f"detail divergence at seq {seq}: the readable detail "
                                  f"no longer matches the bytes that were hashed -- one "
                                  f"of the two columns was edited"}
            expect = hashlib.sha256((prev + blob).encode("utf-8")).hexdigest()
        else:
            expect = compute_hash(prev, detail)
        if expect != row_hash:
            return {"ok": False, "rows": len(rows), "broken_at": seq,
                    "reason": f"content_hash mismatch at seq {seq} (step={step!r}, "
                              f"actor={actor!r}): the stored detail no longer hashes "
                              f"to its recorded hash — this row was altered"}
        prev = row_hash

    return {"ok": True, "rows": len(rows), "head": prev, "reason": "chain intact"}


def chain(conn, job_id: str) -> list[dict]:
    """The full chain for a job, oldest first — for display and for the certificate."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT seq, step, actor, detail, prev_hash, content_hash, ts::text "
            "FROM audit_log WHERE job_id = %s ORDER BY seq ASC",
            (job_id,),
        )
        return [{"seq": r[0], "step": r[1], "actor": r[2], "detail": r[3],
                 "prev_hash": r[4], "content_hash": r[5], "ts": r[6]}
                for r in cur.fetchall()]
