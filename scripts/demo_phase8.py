"""
Phase 8 -- transparency checkpoints.

Phase 5 documented a limitation honestly: a certificate carries the key its own
signature is checked against, so on its own terms it can never prove it is not a
forgery. This closes it with time rather than cryptography alone.

Publish a Merkle root over every chain head. A genuine certificate proves inclusion
in a checkpoint published BEFORE any dispute. A forgery minted afterwards cannot be
included without changing a root that is already public.

    DATABASE_URL=postgresql://... python scripts/demo_phase8.py
"""
from __future__ import annotations

import contextlib
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv                            # noqa: E402
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import psycopg                                            # noqa: E402

DSN = os.environ.get("DATABASE_URL")
if not DSN:
    print("NOT IMPLEMENTED: DATABASE_URL is not set.")
    raise SystemExit(1)

from db import store                                      # noqa: E402


@contextlib.contextmanager
def _direct():
    c = psycopg.connect(DSN, autocommit=True, connect_timeout=10)
    try:
        yield c
    finally:
        c.close()


store.connect = _direct

from core.trust import audit, merkle                      # noqa: E402
from pipelines.document import pipeline                    # noqa: E402

results: list[tuple[bool, str]] = []


def check(name, fn):
    try:
        fn(); results.append((True, name)); print("  PASS  " + name, flush=True)
    except Exception as e:
        results.append((False, name))
        print(f"  FAIL  {name}\n          {type(e).__name__}: {e}", flush=True)


conn = psycopg.connect(DSN, autocommit=True, connect_timeout=10)
WS = "phase8-" + uuid.uuid4().hex[:8]
S = {}


# ── pure tree properties, no database ─────────────────────────────────────
def t_domain_separation():
    """A leaf and an internal node with the same bytes must NOT collide.

    Without domain separation an attacker presents an internal node as a leaf and
    forges an inclusion proof for data never in the tree.
    """
    a, b = merkle.leaf_hash("j", "0" * 64, 1), merkle.leaf_hash("k", "1" * 64, 1)
    internal = merkle.node_hash(a, b)
    fake_leaf = merkle.leaf_hash(a, b, 0)
    assert internal != fake_leaf
    print("          leaf and node hashing are separated", flush=True)


check("domain separation: a node cannot masquerade as a leaf", t_domain_separation)


def t_odd_promotion():
    """Duplicating an odd node lets two leaf sets share a root (CVE-2012-2459)."""
    three = [merkle.leaf_hash(f"j{i}", f"{i}" * 64, 1) for i in range(3)]
    dup = three + [three[-1]]                # what a naive implementation builds
    assert merkle.root(three) != merkle.root(dup), (
        "a duplicated tail produced the same root -- forgeries could inherit proofs")
    print("          3 leaves and 3+duplicate produce DIFFERENT roots", flush=True)


check("odd nodes are promoted, not duplicated", t_odd_promotion)


def t_proof_roundtrip():
    for n in (1, 2, 3, 5, 8, 13):
        leaves = [merkle.leaf_hash(f"j{i}", f"{i:064d}", i) for i in range(n)]
        r = merkle.root(leaves)
        for i in range(n):
            assert merkle.verify_proof(leaves[i], merkle.proof(leaves, i), r), (n, i)
    print("          every leaf of trees sized 1..13 proves inclusion", flush=True)


check("inclusion proofs verify for every leaf at many tree sizes", t_proof_roundtrip)


def t_wrong_leaf_fails():
    leaves = [merkle.leaf_hash(f"j{i}", f"{i:064d}", i) for i in range(6)]
    r, p = merkle.root(leaves), merkle.proof(leaves, 2)
    forged = merkle.leaf_hash("evil", "f" * 64, 1)
    assert not merkle.verify_proof(forged, p, r), "a forged leaf reused a real proof"
    print("          a forged leaf cannot reuse a genuine proof", flush=True)


check("a leaf that was never in the tree fails its proof", t_wrong_leaf_fails)


# ── against the live database ─────────────────────────────────────────────
def t_publish():
    job = pipeline.create_job(conn, "invoice", WS)
    pipeline.ingest_prepared(conn, job, [
        {"name": "invoice_number", "value": "INV-9001", "confidence": 0.97, "recognition": 0.95},
        {"name": "total", "value": "8,400.00", "confidence": 0.99, "recognition": 0.96},
    ], engine="recorded-fixture")
    S["job"] = job
    S["cp"] = merkle.checkpoint(conn, note="phase 8 demo")
    print(f"          checkpoint {S['cp']['checkpoint_id'][:8]}... over "
          f"{S['cp']['leaf_count']} chains", flush=True)
    print(f"          root {S['cp']['merkle_root'][:48]}...", flush=True)


check("publish a checkpoint over every chain in the database", t_publish)


def t_included():
    inc = merkle.inclusion(conn, S["job"], S["cp"]["checkpoint_id"])
    assert inc["ok"], inc
    assert merkle.verify_proof(inc["leaf"], inc["proof"], inc["merkle_root"])
    S["inc"] = inc
    print(f"          leaf {inc['leaf_index']}/{inc['leaf_count']}, "
          f"{len(inc['proof'])}-step proof -- {inc['reason']}", flush=True)


check("the job proves inclusion in the published checkpoint", t_included)


def t_offline_proof():
    """The proof verifies with no database and no server -- the whole point."""
    inc = S["inc"]
    assert merkle.verify_proof(inc["leaf"], inc["proof"], inc["merkle_root"])
    print(f"          recomputed the root from {len(inc['proof'])} sibling hashes alone",
          flush=True)


check("inclusion verifies offline from the leaf and its siblings", t_offline_proof)


def t_tamper_breaks_inclusion():
    """Alter the chain after publication and inclusion must fail."""
    with conn.cursor() as cur:
        cur.execute("SELECT detail_canonical FROM audit_log WHERE job_id=%s AND seq=0",
                    (S["job"],))
        orig = cur.fetchone()[0]
        cur.execute("UPDATE audit_log SET detail=%s, detail_canonical=%s "
                    "WHERE job_id=%s AND seq=0",
                    ('{"engine":"forged"}', '{"engine":"forged"}', S["job"]))
    inc = merkle.inclusion(conn, S["job"], S["cp"]["checkpoint_id"])
    assert not inc["ok"], "a tampered chain still proved inclusion"
    assert inc["recomputed_root"] != inc["merkle_root"]
    print(f"          root moved {inc['merkle_root'][:12]}... -> "
          f"{inc['recomputed_root'][:12]}...", flush=True)
    with conn.cursor() as cur:
        cur.execute("UPDATE audit_log SET detail=%s, detail_canonical=%s "
                    "WHERE job_id=%s AND seq=0", (orig, orig, S["job"]))
    assert merkle.inclusion(conn, S["job"], S["cp"]["checkpoint_id"])["ok"], "restore failed"


check("tampering after publication BREAKS inclusion", t_tamper_breaks_inclusion)


def t_forgery_cannot_backdate():
    """The limitation Phase 5 documented, now closed.

    A forged job created AFTER the checkpoint cannot prove inclusion in it. The
    attacker would have to alter a root that is already published.
    """
    forged = pipeline.create_job(conn, "invoice", WS)
    pipeline.ingest_prepared(conn, forged, [
        {"name": "total", "value": "999,999.00", "confidence": 0.99, "recognition": 0.99},
    ], engine="forged")
    inc = merkle.inclusion(conn, forged, S["cp"]["checkpoint_id"])
    assert not inc["ok"], "a certificate minted after the checkpoint proved inclusion"
    print(f"          forged job: {inc['reason'][:70]}", flush=True)


check("a job created AFTER the checkpoint cannot prove it was in it",
      t_forgery_cannot_backdate)


print("\n  THE CHECKPOINT")
cp = S["cp"]
print(f"    id          {cp['checkpoint_id']}")
print(f"    root        {cp['merkle_root']}")
print(f"    leaves      {cp['leaf_count']} chains")
print(f"    published   {cp['created_at']}")
print("\n  Publish that root anywhere you do not control -- a git commit, a status")
print("  page, a timestamping service. From then on, forging a certificate is not")
print("  a matter of generating a keypair; it is a matter of altering the past.")

conn.close()
bad = [n for ok, n in results if not ok]
print(f"\n  {len(results)-len(bad)}/{len(results)} passed")
for n in bad:
    print(f"    FAILED: {n}")
raise SystemExit(1 if bad else 0)
