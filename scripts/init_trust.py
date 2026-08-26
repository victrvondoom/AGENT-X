"""
Phase 1 self-test -- proves the audit chain detects tampering.

Applies the trust-spine migration, writes a short chain, verifies it, then attacks
it four ways and asserts the verifier catches each one. A verifier that only ever
sees clean data has not been tested; every check below is designed to FAIL if the
verifier is credulous.

Usage:
    DATABASE_URL=postgresql://... python scripts/init_trust.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv                       # noqa: E402

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # pyright: ignore[reportAttributeAccessIssue] -- guarded by hasattr above

import psycopg                                       # noqa: E402
from core.trust import audit                         # noqa: E402

DSN = os.environ.get("DATABASE_URL")
if not DSN:
    print("NOT IMPLEMENTED: DATABASE_URL is not set. Start the database with\n"
          "  docker compose up -d\n"
          "then export DATABASE_URL=postgresql://trustdoc:trustdoc@localhost:5432/trustdoc")
    raise SystemExit(1)

PASS, FAIL = "  PASS  ", "  FAIL  "
results: list[tuple[bool, str]] = []


def check(name: str, fn):
    try:
        fn()
        results.append((True, name))
        print(PASS + name, flush=True)
    except Exception as e:
        results.append((False, name))
        print(FAIL + name + f"\n          {type(e).__name__}: {e}", flush=True)


conn = psycopg.connect(DSN, autocommit=True)

# ── migration ─────────────────────────────────────────────────────────────
def t_migrate():
    path = os.path.join(os.path.dirname(__file__), "..", "db", "migrations",
                        "001_trust_spine.sql")
    with open(path, encoding="utf-8") as f:
        sql = f.read()
    import re
    sql = re.sub(r"--[^\n]*", "", sql)
    n = 0
    for stmt in sql.split(";"):
        if stmt.strip():
            with conn.cursor() as cur:
                # stmt comes from the bundled migration file, not user input; psycopg's
                # stub wants a compile-time LiteralString, which a file read can never be.
                cur.execute(stmt)  # pyright: ignore[reportCallIssue, reportArgumentType]
            n += 1
    print(f"          {n} statements applied", flush=True)


check("migration applies (jobs, fields, audit_log, certificates)", t_migrate)


# ── a job to hang the chain off ───────────────────────────────────────────
JOB = {}


def t_job():
    with conn.cursor() as cur:
        cur.execute("INSERT INTO jobs (kind, doc_type, status) "
                    "VALUES ('document','invoice','EXTRACTING') RETURNING id::text")
        row = cur.fetchone()
        assert row is not None
        JOB["id"] = row[0]
    print(f"          job {JOB['id']}", flush=True)


check("create job", t_job)


# ── write three rows, exactly as the spec asks ────────────────────────────
def t_write():
    a = audit.append_audit(conn, JOB["id"], "upload", "AGENT",
                           {"filename": "invoice-4471.pdf", "bytes": 88213})
    b = audit.append_audit(conn, JOB["id"], "extract", "AGENT",
                           {"fields": 12, "engine": "nutrient-dws/extract"})
    c = audit.append_audit(conn, JOB["id"], "route", "AGENT",
                           {"auto": 9, "human": 3, "threshold": "per-field-type"})
    assert (a["seq"], b["seq"], c["seq"]) == (0, 1, 2), (a, b, c)
    assert a["prev_hash"] == audit.GENESIS
    assert b["prev_hash"] == a["content_hash"], "chain not linked"
    assert c["prev_hash"] == b["content_hash"], "chain not linked"
    print(f"          seq 0..2 linked, head {c['content_hash'][:16]}...", flush=True)


check("append 3 audit rows, each linked to the last", t_write)


# ── the chain verifies clean ──────────────────────────────────────────────
def t_intact():
    r = audit.verify_chain(conn, JOB["id"])
    assert r["ok"], r
    assert r["rows"] == 3, r
    print(f"          {r['rows']} rows, head {r['head'][:16]}... -- {r['reason']}", flush=True)


check("verifier accepts the intact chain", t_intact)


# ── ATTACK 1: alter a row's detail in place ───────────────────────────────
def t_tamper_detail():
    with conn.cursor() as cur:
        cur.execute("UPDATE audit_log SET detail = %s WHERE job_id = %s AND seq = 1",
                    (json.dumps({"fields": 12, "engine": "totally-not-dws"}), JOB["id"]))
    r = audit.verify_chain(conn, JOB["id"])
    assert not r["ok"], "TAMPERING WENT UNDETECTED -- the audit log is worthless"
    assert r["broken_at"] == 1, r
    print(f"          caught: {r['reason']}", flush=True)
    # restore for the next attack
    with conn.cursor() as cur:
        cur.execute("UPDATE audit_log SET detail = %s WHERE job_id = %s AND seq = 1",
                    (json.dumps({"fields": 12, "engine": "nutrient-dws/extract"}), JOB["id"]))
    assert audit.verify_chain(conn, JOB["id"])["ok"], "restore failed"


check("ATTACK -- edit a row's detail -> verifier CATCHES it", t_tamper_detail)


# ── ATTACK 2: rewrite the detail AND its hash, so the row is self-consistent ──
# The naive verifier bug: checking a row's hash against itself passes here.
def t_tamper_consistent():
    forged = {"fields": 12, "engine": "forged"}
    with conn.cursor() as cur:
        cur.execute("SELECT prev_hash FROM audit_log WHERE job_id = %s AND seq = 1",
                    (JOB["id"],))
        row = cur.fetchone()
        assert row is not None
        prev = row[0]
        cur.execute("UPDATE audit_log SET detail = %s, content_hash = %s "
                    "WHERE job_id = %s AND seq = 1",
                    (json.dumps(forged), audit.compute_hash(prev, forged), JOB["id"]))
    r = audit.verify_chain(conn, JOB["id"])
    assert not r["ok"], ("a self-consistent forgery passed -- the verifier is only "
                         "checking each row against itself, not against the chain")
    assert r["broken_at"] == 2, r
    print(f"          caught downstream: {r['reason']}", flush=True)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM audit_log WHERE job_id = %s", (JOB["id"],))
    audit.append_audit(conn, JOB["id"], "upload", "AGENT", {"filename": "invoice-4471.pdf"})
    audit.append_audit(conn, JOB["id"], "extract", "AGENT", {"fields": 12})
    audit.append_audit(conn, JOB["id"], "route", "AGENT", {"auto": 9, "human": 3})


check("ATTACK -- forge detail AND its hash -> still caught by the NEXT row", t_tamper_consistent)


# ── ATTACK 3: delete a row from the middle ────────────────────────────────
def t_delete_middle():
    with conn.cursor() as cur:
        cur.execute("DELETE FROM audit_log WHERE job_id = %s AND seq = 1", (JOB["id"],))
    r = audit.verify_chain(conn, JOB["id"])
    assert not r["ok"], "a deleted row went undetected"
    print(f"          caught: {r['reason']}", flush=True)


check("ATTACK -- delete a middle row -> verifier CATCHES it", t_delete_middle)


# ── ATTACK 4: truncate the tail -- the case hash-chaining alone MISSES ─────
def t_truncate_tail():
    with conn.cursor() as cur:
        cur.execute("DELETE FROM audit_log WHERE job_id = %s", (JOB["id"],))
    for i in range(4):
        audit.append_audit(conn, JOB["id"], f"step-{i}", "AGENT", {"i": i})
    with conn.cursor() as cur:                       # lop off the last entry
        cur.execute("DELETE FROM audit_log WHERE job_id = %s AND seq = 3", (JOB["id"],))
    r = audit.verify_chain(conn, JOB["id"])
    # Being honest about the limit: the remaining chain IS internally valid, which is
    # exactly why the certificate pins the head hash. Verified in Phase 5 -- a
    # truncated tail no longer matches the head the certificate was signed over.
    assert r["ok"], r
    print("          NOTE: a truncated tail leaves a valid chain -- hash-chaining", flush=True)
    print("          cannot see it. The certificate pins the head hash, so this is", flush=True)
    print("          caught at Phase 5 verification, not here.", flush=True)


check("LIMIT -- truncated tail stays internally valid (documented, not hidden)", t_truncate_tail)


# ── out-of-order writes are refused ───────────────────────────────────────
def t_reject_unknown_job():
    try:
        audit.append_audit(conn, "00000000-0000-0000-0000-000000000000",
                           "ghost", "AGENT", {})
    except ValueError:
        return
    raise AssertionError("appended to a nonexistent job")


check("append to unknown job is refused", t_reject_unknown_job)


def t_reject_bad_actor():
    try:
        audit.append_audit(conn, JOB["id"], "x", "ROBOT", {})
    except ValueError:
        return
    raise AssertionError("accepted an unknown actor")


check("append with an unknown actor is refused", t_reject_bad_actor)

with conn.cursor() as cur:
    cur.execute("DELETE FROM jobs WHERE id = %s", (JOB["id"],))
conn.close()

bad = [n for ok, n in results if not ok]
print(f"\n  {len(results) - len(bad)}/{len(results)} passed")
for n in bad:
    print(f"    FAILED: {n}")
raise SystemExit(1 if bad else 0)
