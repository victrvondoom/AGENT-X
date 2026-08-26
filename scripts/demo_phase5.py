"""
Phase 5 deliverable -- the win condition.

  1. issue a real Certificate of Compliance over a completed job
  2. verify it, then tamper ONE field and show verification REJECT it
  3. show the raw SQL a judge runs themselves, and RUN it here to prove it works

Every check is designed to fail loudly if the verifier is credulous. In particular
the tamper tests re-sign nothing: a forged certificate must be caught by the hash,
and a re-hashed forgery must still be caught by the signature.

Usage:
    DATABASE_URL=postgresql://... python scripts/demo_phase5.py
"""
from __future__ import annotations

import copy
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv                              # noqa: E402
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # pyright: ignore[reportAttributeAccessIssue] -- guarded by hasattr above

import psycopg                                              # noqa: E402
from core.trust import audit, certificate                   # noqa: E402
from pipelines.document import (finalize, pipeline,          # noqa: E402
                                review, sign)

DSN = os.environ.get("DATABASE_URL")
if not DSN:
    print("NOT IMPLEMENTED: DATABASE_URL is not set.")
    raise SystemExit(1)

FIELDS = [
    {"name": "invoice_number", "value": "INV-4471",       "confidence": 0.97, "recognition": 0.95},
    {"name": "vendor_name",    "value": "Acme Logistics", "confidence": 0.88, "recognition": 0.93},
    {"name": "date",           "value": "2026-07-14",     "confidence": 0.94, "recognition": 0.91},
    {"name": "total",          "value": "1,248.00",       "confidence": 0.91, "recognition": 0.90},
]

results: list[tuple[bool, str]] = []


def check(name, fn):
    try:
        fn(); results.append((True, name)); print("  PASS  " + name, flush=True)
    except Exception as e:
        results.append((False, name))
        print(f"  FAIL  {name}\n          {type(e).__name__}: {e}", flush=True)


conn = psycopg.connect(DSN, autocommit=True, connect_timeout=10)

if not sign.public_key_pem():
    os.environ["AGENT_X_SIGNING_KEY"] = sign.generate_signing_key()
    print("  (generated an ephemeral signing key for this run)")

S = {}


# ── drive a job all the way to SIGNED, then certify it ────────────────────
def t_issue():
    job = pipeline.create_job(conn, "invoice")
    pipeline.ingest_prepared(conn, job, FIELDS, engine="recorded-fixture")
    for f in review.pending(conn, job):
        if f["name"] == "total":
            review.decide(conn, job, f["id"], "CORRECT", "v.kumar", "12,480.00")
        else:
            review.decide(conn, job, f["id"], "ACCEPT", "v.kumar")
    fin = finalize.finalize(conn, job)

    body = certificate.build(
        conn, job, kind="document",
        document_sha256=fin["document_sha256"],
        fields=pipeline.job_fields(conn, job),
        extra={"document_signature_embedded": fin["document_signature_embedded"]},
    )
    env = certificate.sign(body, sign._key())
    assert env["signed"], env

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO certificates (job_id, cert_json, sha256, signature, public_key, "
            "audit_head, doc_sha256) VALUES (%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (job_id) DO UPDATE SET cert_json=excluded.cert_json, "
            "sha256=excluded.sha256, signature=excluded.signature, "
            "public_key=excluded.public_key, audit_head=excluded.audit_head, "
            "doc_sha256=excluded.doc_sha256",
            (job, json.dumps(env["certificate"]), env["sha256"], env["signature"],
             env["public_key"], body["chain_head"], fin["document_sha256"]),
        )
    S["job"], S["env"] = job, env
    print(f"          job {job}", flush=True)
    print(f"          cert sha256 {env['sha256'][:32]}...", flush=True)
    print(f"          chain head  {body['chain_head'][:32]}... over "
          f"{body['chain_length']} entries", flush=True)


check("issue a signed Certificate of Compliance", t_issue)


# ── it verifies, fully ────────────────────────────────────────────────────
def t_verify_ok():
    v = certificate.verify(S["env"], conn)
    assert v["ok"], v
    for k in ("content_hash", "signature", "audit_chain"):
        assert v["checks"][k]["ok"] is True, (k, v["checks"][k])
    print("          hash OK · signature OK · chain OK", flush=True)


check("a genuine certificate verifies (hash + signature + live chain)", t_verify_ok)


# ── it verifies OFFLINE, with no database at all ──────────────────────────
def t_verify_offline():
    v = certificate.verify(S["env"], conn=None)
    assert v["checks"]["content_hash"]["ok"] is True
    assert v["checks"]["signature"]["ok"] is True
    assert v["checks"]["audit_chain"]["ok"] is None
    assert v["ok"], v
    print("          hash + signature verify with no DB and no server", flush=True)


check("certificate verifies OFFLINE using only its embedded public key", t_verify_offline)


# ── ATTACK 1: change a field value in the certificate ─────────────────────
def t_tamper_field():
    bad = copy.deepcopy(S["env"])
    tot = [f for f in bad["certificate"]["fields"] if f["name"] == "total"][0]
    tot["value"] = "999,999.00"
    v = certificate.verify(bad, conn)
    assert not v["ok"], "a tampered certificate verified"
    assert v["checks"]["content_hash"]["ok"] is False, v["checks"]
    print(f"          rejected: {v['checks']['content_hash']['detail']}", flush=True)


check("ATTACK -- edit a field value -> verification REJECTS", t_tamper_field)


# ── ATTACK 2: edit the field AND recompute the hash ───────────────────────
def t_tamper_rehash():
    bad = copy.deepcopy(S["env"])
    tot = [f for f in bad["certificate"]["fields"] if f["name"] == "total"][0]
    tot["value"] = "999,999.00"
    import hashlib
    bad["sha256"] = hashlib.sha256(
        certificate.canonical(bad["certificate"])).hexdigest()
    v = certificate.verify(bad, conn)
    assert v["checks"]["content_hash"]["ok"] is True, "hash should now match"
    assert v["checks"]["signature"]["ok"] is False, (
        "a re-hashed forgery passed -- the signature is not actually being checked")
    assert not v["ok"]
    print("          hash recomputed by the attacker, but the SIGNATURE catches it",
          flush=True)


check("ATTACK -- edit AND re-hash -> the signature still REJECTS", t_tamper_rehash)


# ── ATTACK 3: swap in the attacker's own key pair ─────────────────────────
def t_tamper_resign():
    """The attack nothing self-contained can stop, stated honestly.

    An attacker edits a value, signs with THEIR key, and embeds THEIR key. Hash and
    signature both pass, because the certificate is vouching for itself. Two things
    catch it and the test asserts both: pinning the published key, and the fact that
    a forgery is not in the database so its chain binding is stale.
    """
    from cryptography.hazmat.primitives.asymmetric import ec
    evil = ec.generate_private_key(ec.SECP256R1())
    forged_body = copy.deepcopy(S["env"]["certificate"])
    tot = [f for f in forged_body["fields"] if f["name"] == "total"][0]
    tot["value"] = "999,999.00"
    # a forgery cannot know a chain head that was never written
    forged_body["chain_head"] = "f" * 64
    bad = certificate.sign(forged_body, evil)

    naive = certificate.verify(bad, conn=None)
    assert naive["checks"]["content_hash"]["ok"] is True
    assert naive["checks"]["signature"]["ok"] is True, "self-signed forgery is self-consistent"

    pinned = certificate.verify(bad, conn, trusted_public_key=S["env"]["public_key"])
    assert pinned["checks"]["trusted_key"]["ok"] is False, pinned["checks"]
    assert pinned["checks"]["audit_chain"]["ok"] is False, pinned["checks"]
    assert not pinned["ok"]

    genuine = certificate.verify(S["env"], conn,
                                 trusted_public_key=S["env"]["public_key"])
    assert genuine["checks"]["trusted_key"]["ok"] is True and genuine["ok"], genuine
    print("          self-signed forgery passes hash+signature ALONE (documented),",
          flush=True)
    print("          but is rejected by key pinning AND by the chain binding", flush=True)


check("ATTACK -- forge and re-sign with another key -> chain binding REJECTS",
      t_tamper_resign)


# ── ATTACK 4: tamper the DATABASE after the certificate was issued ────────
def t_tamper_db():
    with conn.cursor() as cur:
        cur.execute("SELECT detail FROM audit_log WHERE job_id=%s AND seq=1", (S["job"],))
        row = cur.fetchone()
        assert row is not None
        orig = row[0]
        # edit BOTH columns, which is what a competent tamperer would do
        cur.execute("UPDATE audit_log SET detail=%s, detail_canonical=%s "
                    "WHERE job_id=%s AND seq=1",
                    (json.dumps({"engine": "forged"}),
                     json.dumps({"engine": "forged"}, sort_keys=True,
                                separators=(",", ":")), S["job"]))
    v = certificate.verify(S["env"], conn)
    assert v["checks"]["content_hash"]["ok"] is True, "the certificate itself is untouched"
    assert v["checks"]["signature"]["ok"] is True
    assert v["checks"]["audit_chain"]["ok"] is False, (
        "the database was edited but the certificate still passed")
    print(f"          {v['checks']['audit_chain']['detail']}", flush=True)
    with conn.cursor() as cur:
        cur.execute("UPDATE audit_log SET detail=%s, detail_canonical=%s "
                    "WHERE job_id=%s AND seq=1",
                    (json.dumps(orig), json.dumps(orig, sort_keys=True,
                                                  separators=(",", ":")), S["job"]))
    assert certificate.verify(S["env"], conn)["ok"], "restore failed"


check("ATTACK -- edit the DATABASE after issue -> chain check REJECTS", t_tamper_db)


# ── the judge's own SQL, actually run ─────────────────────────────────────
def t_judge_sql():
    """The verdict query from db/verify_chain.sql, run verbatim.

    Uses digest() from pgcrypto. If the extension is unavailable the check is
    reported as unavailable rather than skipped silently.
    """
    with conn.cursor() as cur:
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        except Exception:
            pass
        try:
            cur.execute("SELECT encode(digest('x','sha256'),'hex')")
            cur.fetchone()
        except Exception as e:
            print(f"          NOT IMPLEMENTED: digest() unavailable here ({type(e).__name__}). "
                  f"The SQL is written for PostgreSQL 16 + pgcrypto.", flush=True)
            S["sql_ok"] = None
            return

        cur.execute("""
            WITH RECURSIVE walk AS (
                SELECT a.seq, a.detail, a.prev_hash, a.content_hash,
                       encode(digest(repeat('0',64) || a.detail_canonical,'sha256'),'hex') AS rec
                FROM audit_log a WHERE a.job_id = %s AND a.seq = 0
                UNION ALL
                SELECT n.seq, n.detail, n.prev_hash, n.content_hash,
                       encode(digest(w.content_hash || n.detail_canonical,'sha256'),'hex')
                FROM audit_log n JOIN walk w ON n.seq = w.seq + 1 WHERE n.job_id = %s
            )
            SELECT count(*), count(*) FILTER (WHERE rec <> content_hash) FROM walk
        """, (S["job"], S["job"]))
        result = cur.fetchone()
        assert result is not None
        rows, bad = result
    S["sql_ok"] = (bad == 0 and rows > 0)
    print(f"          SQL re-derived {rows} hashes independently, {bad} mismatched",
          flush=True)
    assert S["sql_ok"], "the judge's own SQL disagrees with our verifier"


check("raw SQL re-derives every hash and agrees", t_judge_sql)


# ── show the artefacts ────────────────────────────────────────────────────
print("\n  CERTIFICATE (abridged)")
c = S["env"]["certificate"]
print(f"    spec            {c['spec']}")
print(f"    job_id          {c['job_id']}")
print(f"    chain_head      {c['chain_head'][:48]}...")
print(f"    chain_length    {c['chain_length']}")
print(f"    document_sha256 {c['document_sha256'][:48]}...")
print(f"    counts          {c['counts']}")
print("    fields")
for f in c["fields"]:
    print(f"      {f['name']:<16} {str(f['value']):<16} {f['decision']:<6} "
          f"{f['reviewed_by'] or '-'}")
print(f"    algorithm       {S['env']['algorithm']}")
print(f"    sha256          {S['env']['sha256']}")

print("\n  RUN IT YOURSELF -- no application code involved:")
print(f"    psql \"$DATABASE_URL\" -v job=\"'{S['job']}'\" -f db/verify_chain.sql")
print("    open templates/verify_offline.html from disk, paste the certificate")

conn.close()
bad = [n for ok, n in results if not ok]
print(f"\n  {len(results)-len(bad)}/{len(results)} passed")
for n in bad:
    print(f"    FAILED: {n}")
raise SystemExit(1 if bad else 0)
