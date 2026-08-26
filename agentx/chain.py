"""
The per-case chain — Agent X's audit primitive, applied to a consumer case.

Every case carries a gap-free, hash-linked record of its own life:

    what the user said · what evidence arrived · what Agent X inferred ·
    which policies were considered · what was proposed · what the user
    authorised · what was executed · what the external system returned ·
    what was verified · what was deleted

The chain rule is the one already in `core/trust/audit.py`, unchanged and imported
rather than reimplemented: `content_hash = sha256(prev_hash || canonical(detail))`,
with a per-case sequence that is contiguous so a deleted row leaves a hole rather
than a still-valid shorter chain. Sharing the primitive is the point — a verifier
that can check an erasure certificate can check a resolution receipt, because they
are the same construction.

WHAT IS DIFFERENT HERE, AND WHY

The trust spine's `audit_log` hangs off a `jobs` row and lives only on
CockroachDB. A consumer case has to be verifiable wherever it runs, including on
the local engine, so the chain has its own portable table. Where CockroachDB IS
present, the case additionally opens a spine `jobs` row and mirrors its lifecycle
there, so a case and an erasure share one trail rather than two — see
`agentx/case.py`.

SEALING

Sensitive detail is sealed under the case's key exactly as `core/trust/sealed.py`
does for documents: the envelope is hashed, so destroying the key later leaves
every hash valid and the content unrecoverable. Step names, actors, timestamps and
ordering stay in clear text, because after an erasure a reader must still be able
to see that a refund was requested on a date and confirmed on another — sealing
the shape of the record as well as its contents would destroy the audit trail in
the name of protecting it.
"""
from __future__ import annotations

import hashlib
import json
from typing import cast

from core.trust.audit import GENESIS, canonical, compute_hash
from agentx import ids, sealing

ACTORS = ("AGENT", "HUMAN", "SYSTEM", "EXTERNAL")

# Keys that stay readable after a crypto-shred. Everything else is presumed to be
# personal and goes inside the envelope.
CLEAR_KEYS = {
    "step", "actor", "status", "state", "from_state", "to_state", "result",
    "action", "provider", "provider_mode", "capability", "decision", "reason",
    "because", "policy", "policy_id", "applies", "risk", "level", "autonomy",
    "severity", "kind", "problem_type", "domain", "confidence", "posterior",
    "engine", "verified", "count", "counts", "total", "auto", "human",
    "sha256", "chain_head", "chain_length", "plan_id", "step_key", "granted",
    "outcome", "elapsed_days", "attempt", "max_attempts", "policy_snapshot",
}

TOMBSTONE = "<crypto-shredded: the key for this case was destroyed>"


def _split(detail: dict) -> tuple[dict, dict]:
    clear, sensitive = {}, {}
    for k, v in (detail or {}).items():
        (clear if k in CLEAR_KEYS else sensitive)[k] = v
    return clear, sensitive


def head(conn, case_id: str) -> tuple[int, str]:
    with conn.cursor() as cur:
        cur.execute("SELECT seq, content_hash FROM case_chain WHERE case_id = %s"
                    " ORDER BY seq DESC LIMIT 1", (case_id,))
        row = cur.fetchone()
    return (int(row[0]), row[1]) if row else (-1, GENESIS)


def append(conn, case_id: str, step: str, actor: str, detail: dict, *,
           seal: bool = False, subject: str | None = None,
           workspace: str = "default") -> dict:
    """Append one row. Returns its seq and hash.

    An unknown actor raises rather than writing an unclassifiable row, for the
    same reason the trust spine does: an entry whose actor cannot be trusted is
    worse than no entry, because it still looks like evidence.
    """
    if actor not in ACTORS:
        raise ValueError(f"unknown actor {actor!r} — must be one of {ACTORS}")

    if seal and subject:
        clear, sensitive = _split(detail)
        envelope: dict = {"_clear": clear, "_sealed": None}
        if sensitive:
            try:
                envelope["_sealed"] = sealing.seal_json(conn, workspace, subject, sensitive)
            except sealing.KeyDestroyed:
                # The case was already erased. Writing the values back now would
                # recreate data we are required not to hold.
                envelope["_clear"]["sealed_omitted"] = "case key already destroyed"
        payload = envelope
    else:
        payload = detail

    last_seq, prev = head(conn, case_id)
    seq = last_seq + 1
    blob = canonical(payload)
    content_hash = hashlib.sha256((prev + blob).encode("utf-8")).hexdigest()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO case_chain (id, case_id, seq, step, actor, detail, prev_hash,"
            " content_hash, sealed, seal_subject, seal_workspace, ts)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (ids.new("cc"), case_id, seq, step, actor, blob, prev, content_hash,
             bool(seal and subject), subject if seal else None,
             workspace if seal else None, ids.now()))
    return {"seq": seq, "prev_hash": prev, "content_hash": content_hash,
            "sealed": bool(seal and subject)}


def rows(conn, case_id: str) -> list[dict]:
    cols = ["seq", "step", "actor", "detail", "prev_hash", "content_hash", "sealed",
            "seal_subject", "seal_workspace", "ts"]
    with conn.cursor() as cur:
        cur.execute(
            "SELECT seq, step, actor, detail, prev_hash, content_hash, sealed,"
            " seal_subject, seal_workspace, ts FROM case_chain WHERE case_id = %s"
            " ORDER BY seq ASC", (case_id,))
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def verify(conn, case_id: str) -> dict:
    """Re-walk the chain, recomputing every hash from the stored pre-image.

    Recomputed, never compared against itself: checking a stored hash against the
    stored hash passes on any tampered row. Reports the FIRST break, because
    everything after one is unreliable anyway.
    """
    chain = rows(conn, case_id)
    if not chain:
        return {"ok": True, "rows": 0, "head": GENESIS, "reason": "empty chain"}
    prev = GENESIS
    for i, r in enumerate(chain):
        if int(r["seq"]) != i:
            return {"ok": False, "rows": len(chain), "broken_at": r["seq"],
                    "reason": f"sequence gap: expected {i}, found {r['seq']} "
                              f"(a row was deleted or inserted out of order)"}
        if r["prev_hash"] != prev:
            return {"ok": False, "rows": len(chain), "broken_at": r["seq"],
                    "reason": f"prev_hash mismatch at seq {r['seq']}: the chain was re-linked"}
        expect = hashlib.sha256((prev + r["detail"]).encode("utf-8")).hexdigest()
        if expect != r["content_hash"]:
            return {"ok": False, "rows": len(chain), "broken_at": r["seq"],
                    "reason": f"content_hash mismatch at seq {r['seq']} "
                              f"(step={r['step']!r}, actor={r['actor']!r}): this row was altered"}
        prev = r["content_hash"]
    return {"ok": True, "rows": len(chain), "head": prev, "reason": "chain intact"}


def verify_inclusion(conn, case_id: str, *, seq: int | None, content_hash: str | None) -> dict:
    """Was this (seq, content_hash) pair really written to this case's chain?

    Built for one specific problem: an attestation (a receipt, an evidence
    package) records the chain's head at the moment it was signed, and then the
    act of STORING that attestation appends one more row — "receipt.issued" — to
    the very case it just described. From that instant on, the live chain is
    always one row ahead of what the attestation says, forever. Comparing head-
    to-head or length-to-length would make every stored receipt fail its own
    verification the moment it exists.

    Inclusion checking is the fix, and it is the same idea the Merkle checkpoints
    in `core/trust/merkle.py` use for the mirror-image problem (a certificate that
    cannot vouch for its own genuineness). Growth after the attested point is
    normal and expected. What must NEVER happen is the row at that exact position
    changing underneath the attestation — that is truncation or tampering, and
    this still catches both: a chain shorter than `seq` means the row is gone, and
    a mismatched hash at that position means it was altered.
    """
    if seq is None or content_hash is None:
        return {"ok": False, "detail": "no attested position to check"}
    live = verify(conn, case_id)
    if not live.get("ok"):
        return {"ok": False, "detail": f"the live chain is broken: {live.get('reason')}"}
    if live["rows"] <= seq:
        return {"ok": False,
                "detail": f"the chain now has only {live['rows']} row(s); the "
                          f"attested position {seq} no longer exists — the chain "
                          f"was truncated after this was issued"}
    with conn.cursor() as cur:
        cur.execute("SELECT content_hash FROM case_chain WHERE case_id = %s AND seq = %s",
                    (case_id, seq))
        row = cur.fetchone()
    if not row or row[0] != content_hash:
        return {"ok": False,
                "detail": "the row at the attested position no longer matches the "
                          "attested hash — history at that point was rewritten"}
    return {"ok": True,
            "detail": f"included at position {seq}; the chain has grown to "
                      f"{live['rows']} row(s) since, which is expected"}


def digest(conn, case_id: str) -> str:
    """A single hash over the chain's entire content, not just its head.

    Head-only commitments miss an edit in the MIDDLE of a chain when the last row
    is untouched — the trust spine found this the hard way and the same lesson
    applies here.
    """
    d = hashlib.sha256()
    for r in rows(conn, case_id):
        d.update(f"{r['seq']}|{r['prev_hash']}|{r['content_hash']}|{r['detail']}".encode())
    return d.hexdigest()


def readable(conn, case_id: str) -> list[dict]:
    """The chain for display, with sealed rows opened where the key still exists."""
    out = []
    for r in rows(conn, case_id):
        detail = r["detail"]
        parsed: object
        try:
            parsed = json.loads(detail) if isinstance(detail, str) else detail
        except (TypeError, ValueError):
            # cast, not a plain literal: an unannotated {"_unreadable": True} is
            # inferred as dict[str, bool] and that narrow shape leaks into every
            # later isinstance(parsed, dict) branch below, breaking unrelated dict
            # operations on parsed with nonsense inferred key/value types.
            parsed = cast(object, {"_unreadable": True})
        shredded = False
        if r["sealed"] and isinstance(parsed, dict) and "_clear" in parsed:
            view = dict(parsed.get("_clear") or {})
            blob = parsed.get("_sealed")
            if blob:
                opened = sealing.unseal_json(conn, r["seal_workspace"] or "default",
                                             r["seal_subject"], blob)
                if opened is None:
                    view["_shredded"] = TOMBSTONE
                    shredded = True
                elif isinstance(opened, dict):
                    view.update(opened)
            parsed = view
        out.append({"seq": r["seq"], "step": r["step"], "actor": r["actor"],
                    "ts": r["ts"], "content_hash": r["content_hash"],
                    "prev_hash": r["prev_hash"], "sealed": bool(r["sealed"]),
                    "shredded": shredded, "detail": parsed})
    return out


def public_summary(conn, case_id: str) -> dict:
    v = verify(conn, case_id)
    return {"case_id": case_id, "rows": v.get("rows"), "head": v.get("head"),
            "intact": v.get("ok"), "reason": v.get("reason"),
            "content_digest": digest(conn, case_id)}
