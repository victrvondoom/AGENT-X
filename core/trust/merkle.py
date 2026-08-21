"""
Merkle transparency checkpoints — closing the self-vouching gap.

The limitation documented in Phase 5 is real and unavoidable on its own terms: a
certificate carries the public key its signature is checked against, so anyone can
mint one that is internally valid. Hash and signature both pass. Nothing
self-contained detects a forged document with forged letterhead.

What DOES detect it is time. Periodically we compute a Merkle root over every
chain head in the database and publish it. A genuine certificate can then prove it
was included in a checkpoint published BEFORE the dispute; a forgery created later
cannot be, because inserting it would change a root that is already published.

Forging a certificate stops being a matter of generating a keypair and becomes a
matter of altering the past.

TWO IMPLEMENTATION DETAILS THAT ARE NOT OPTIONAL

  * Domain separation. Leaves are hashed with a 0x00 prefix and internal nodes with
    0x01. Without it an attacker can present an internal node as if it were a leaf
    -- the classic second-preimage attack on Merkle trees -- and forge an inclusion
    proof for data that was never in the tree.
  * Odd nodes are PROMOTED, not duplicated. Duplicating the last node lets two
    different leaf sets produce the same root (CVE-2012-2459, the Bitcoin
    duplicate-transaction bug), which would let a forgery inherit a real proof.
"""
from __future__ import annotations

import hashlib

LEAF, NODE = b"\x00", b"\x01"


def _h(*parts: bytes) -> str:
    d = hashlib.sha256()
    for p in parts:
        d.update(p)
    return d.hexdigest()


def leaf_hash(job_id: str, content_digest: str, length: int) -> str:
    """A leaf commits to the job, a digest of its ENTIRE content, and its length.

    Committing only to the head hash is not enough, and the test that found this
    is worth keeping in mind: editing a row in the MIDDLE without touching the
    last row leaves the head unchanged, so a head-only leaf still matched a
    published checkpoint. verify_chain caught it, but the checkpoint alone did not
    -- and the checkpoint is the artefact a third party holds.

    `content_digest` therefore covers every row's seq, links and hashed pre-image,
    so altering any byte anywhere in the chain moves the leaf and breaks inclusion.

    Length remains because a chain truncated from the tail is internally valid.
    """
    return _h(LEAF, f"{job_id}:{content_digest}:{length}".encode())


def chain_digest(conn, job_id: str) -> str:
    """A single hash over a chain's whole content, not just its head."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT seq, prev_hash, content_hash, COALESCE(detail_canonical, '') "
            "FROM audit_log WHERE job_id = %s ORDER BY seq ASC",
            (job_id,),
        )
        rows = cur.fetchall()
    d = hashlib.sha256()
    for seq, prev, ch, blob in rows:
        d.update(f"{seq}|{prev}|{ch}|{blob}".encode("utf-8"))
    return d.hexdigest()


def node_hash(left: str, right: str) -> str:
    return _h(NODE, bytes.fromhex(left), bytes.fromhex(right))


def build(leaves: list[str]) -> list[list[str]]:
    """Build the tree bottom-up. Returns levels, leaves first, root last."""
    if not leaves:
        return [[_h(LEAF, b"empty")]]
    levels = [list(leaves)]
    while len(levels[-1]) > 1:
        cur, nxt = levels[-1], []
        for i in range(0, len(cur) - 1, 2):
            nxt.append(node_hash(cur[i], cur[i + 1]))
        if len(cur) % 2:
            nxt.append(cur[-1])          # PROMOTE the odd one; never duplicate
        levels.append(nxt)
    return levels


def root(leaves: list[str]) -> str:
    return build(leaves)[-1][0]


def proof(leaves: list[str], index: int) -> list[dict]:
    """Sibling path for `index`: the minimum needed to recompute the root."""
    if not 0 <= index < len(leaves):
        raise IndexError("leaf index out of range")
    path, levels, i = [], build(leaves), index
    for level in levels[:-1]:
        if i % 2 == 0:
            if i + 1 < len(level):
                path.append({"side": "right", "hash": level[i + 1]})
            # else: promoted, no sibling at this level
        else:
            path.append({"side": "left", "hash": level[i - 1]})
        i //= 2
    return path


def verify_proof(leaf: str, path: list[dict], expected_root: str) -> bool:
    """Recompute the root from a leaf and its path. No tree, no database needed."""
    h = leaf
    for step in path:
        if step["side"] == "right":
            h = node_hash(h, step["hash"])
        elif step["side"] == "left":
            h = node_hash(step["hash"], h)
        else:
            return False
    return h == expected_root


# ── database side ─────────────────────────────────────────────────────────
def collect(conn) -> list[tuple[str, str, int]]:
    """Every job's (id, content_digest, length), ordered so the tree is reproducible.

    Ordered by job id rather than by time: a checkpoint must be recomputable by
    anyone holding the same data, and insertion order is not something a third
    party can observe.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT id::text FROM jobs ORDER BY id::text")
        job_ids = [r[0] for r in cur.fetchall()]
    out = []
    for jid in job_ids:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM audit_log WHERE job_id = %s", (jid,))
            n = int(cur.fetchone()[0])
        out.append((jid, chain_digest(conn, jid), n))
    return out


def checkpoint(conn, note: str = "") -> dict:
    """Compute and store a checkpoint over the current state of every chain."""
    entries = collect(conn)
    leaves = [leaf_hash(*e) for e in entries]
    r = root(leaves)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO checkpoints (merkle_root, leaf_count, note) "
            "VALUES (%s, %s, %s) RETURNING id::text, created_at::text",
            (r, len(leaves), note),
        )
        cid, at = cur.fetchone()
    return {"checkpoint_id": cid, "merkle_root": r, "leaf_count": len(leaves),
            "created_at": at, "note": note}


def inclusion(conn, job_id: str, checkpoint_id: str | None = None) -> dict:
    """An inclusion proof for one job against a stored checkpoint.

    Recomputes the tree from current state, so a proof only verifies while the job's
    chain still matches what the checkpoint committed to. If the chain has since
    been altered, the leaf changes and the proof fails -- which is the detection we
    want, not a bug.
    """
    with conn.cursor() as cur:
        if checkpoint_id:
            cur.execute("SELECT id::text, merkle_root, created_at::text FROM checkpoints "
                        "WHERE id = %s", (checkpoint_id,))
        else:
            cur.execute("SELECT id::text, merkle_root, created_at::text FROM checkpoints "
                        "ORDER BY created_at DESC LIMIT 1")
        row = cur.fetchone()
    if row is None:
        return {"ok": False, "reason": "no checkpoint has been published yet"}

    cid, expected, at = row
    entries = collect(conn)
    leaves = [leaf_hash(*e) for e in entries]
    idx = next((i for i, e in enumerate(entries) if e[0] == job_id), None)
    if idx is None:
        return {"ok": False, "checkpoint_id": cid,
                "reason": f"job {job_id} is not in the tree"}

    p = proof(leaves, idx)
    ok = verify_proof(leaves[idx], p, expected) and root(leaves) == expected
    return {
        "ok": ok,
        "checkpoint_id": cid,
        "published_at": at,
        "merkle_root": expected,
        "recomputed_root": root(leaves),
        "leaf": leaves[idx],
        "leaf_index": idx,
        "leaf_count": len(leaves),
        "proof": p,
        "reason": ("included in the published checkpoint" if ok else
                   "NOT included -- the chain has changed since this checkpoint was "
                   "published, or this job was never in it"),
    }
