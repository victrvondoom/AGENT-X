"""
Phase 4 deliverable -- a full run producing a signed doc with self-verify PASS,
then a deliberately corrupted run showing the loop CATCH it.

The corruption is injected AFTER human approval and BEFORE the re-read, which is
exactly the window the loop exists to police: a document that no longer says what a
human agreed to.

Usage:
    DATABASE_URL=postgresql://... python scripts/demo_phase4.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv                          # noqa: E402
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import psycopg                                          # noqa: E402
from core.trust import audit                            # noqa: E402
from pipelines.document import (dws, finalize, generate, # noqa: E402
                                pipeline, review, sign)

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

print(f"\n  DWS: {'configured' if dws.configured() else 'NOT configured'}")
if not dws.configured():
    print("  NOT IMPLEMENTED: POST /sign (document signature) -- needs DWS_API_KEY.")
    print("  A detached ECDSA signature over the document hash is still produced.\n")


def drive_to_approved() -> str:
    """Run a job through extraction, routing and human review."""
    job = pipeline.create_job(conn, "invoice")
    pipeline.ingest_prepared(conn, job, FIELDS, engine="recorded-fixture")
    for f in review.pending(conn, job):
        # correct the dropped digit exactly as a reviewer did in Phase 3
        if f["name"] == "total":
            review.decide(conn, job, f["id"], "CORRECT", "v.kumar", "12,480.00")
        else:
            review.decide(conn, job, f["id"], "ACCEPT", "v.kumar")
    assert review.progress(conn, job)["status"] == "APPROVED"
    return job


# ══ RUN 1 -- clean ════════════════════════════════════════════════════════
CLEAN = {}


def t_clean():
    job = drive_to_approved()
    CLEAN["job"] = job
    out = finalize.finalize(conn, job)
    CLEAN["out"] = out
    assert out["status"] == "SIGNED", out
    assert out["self_verify"] == "PASS", out
    print(f"          job {job}", flush=True)
    print(f"          document {out['bytes']} bytes  sha256 {out['document_sha256'][:24]}...",
          flush=True)
    print(f"          detached signature: {out['detached_signature']['algorithm']}",
          flush=True)
    print(f"          embedded PDF signature: {out['document_signature_embedded']}",
          flush=True)


check("clean run -> SIGNED with self-verify PASS", t_clean)


def t_readback_is_real():
    """The loop must parse the artefact, not remember what it wrote."""
    pdf = CLEAN["out"]["pdf"]
    got = generate.read_pdf_fields(pdf)
    assert got.get("total") == "12,480.00", got
    # flip a byte inside the file and the read-back must change
    i = pdf.find(b"12,480.00")
    assert i > 0, "the value is not actually in the file"
    mangled = pdf[:i] + b"99,999.99" + pdf[i + 9:]
    assert generate.read_pdf_fields(mangled).get("total") == "99,999.99"
    print("          re-read parses the real PDF stream (byte-flip changes it)", flush=True)


check("self-verify re-reads the artefact, not memory", t_readback_is_real)


def t_signature_verifies():
    d = CLEAN["out"]["detached_signature"]
    assert d["signed"], d
    ok = sign.verify_detached(CLEAN["out"]["document_sha256"].encode(),
                              d["signature"], d["public_key"])
    assert ok, "detached signature failed to verify"
    bad = sign.verify_detached(b"a" * 64, d["signature"], d["public_key"])
    assert not bad, "signature verified against the WRONG payload"
    print("          detached ECDSA verifies, and rejects a wrong payload", flush=True)


check("detached signature verifies offline with the public key alone", t_signature_verifies)


def t_audit_pass():
    ch = audit.chain(conn, CLEAN["job"])
    sv = [e for e in ch if e["step"] == "self-verify"]
    assert len(sv) == 1 and sv[0]["detail"]["result"] == "PASS", sv
    assert audit.verify_chain(conn, CLEAN["job"])["ok"]
    print(f"          audit entry: self-verify PASS ({sv[0]['detail']['checked']} fields)",
          flush=True)


check("audit chain carries 'self-verify: PASS'", t_audit_pass)


# ══ RUN 2 -- deliberately corrupted ═══════════════════════════════════════
DIRTY = {}


def t_corrupt_caught():
    job = drive_to_approved()
    DIRTY["job"] = job
    try:
        finalize.finalize(conn, job, corrupt=("total", "999,999.00"))
    except finalize.SelfVerifyFailed as e:
        DIRTY["mismatches"] = e.mismatches
        assert len(e.mismatches) == 1, e.mismatches
        m = e.mismatches[0]
        assert m["field"] == "total" and m["problem"] == "value_mismatch", m
        assert m["approved"] == "12,480.00" and m["found"] == "999,999.00", m
        print(f"          CAUGHT: {m['field']} approved {m['approved']!r} "
              f"but document says {m['found']!r}", flush=True)
        return
    raise AssertionError("a corrupted document shipped silently -- the loop is broken")


check("corrupted document -> self-verify CATCHES it", t_corrupt_caught)


def t_failed_state():
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM jobs WHERE id = %s", (DIRTY["job"],))
        assert cur.fetchone()[0] == "FAILED"
    ch = audit.chain(conn, DIRTY["job"])
    sv = [e for e in ch if e["step"] == "self-verify"][0]
    assert sv["detail"]["result"] == "FAIL"
    assert sv["detail"]["mismatches"][0]["field"] == "total"
    assert audit.verify_chain(conn, DIRTY["job"])["ok"], "chain broke on the failure path"
    print("          status FAILED, mismatch named in the audit entry", flush=True)


check("failed job is FAILED, and the chain records which field", t_failed_state)


def t_refuse_unapproved():
    job = pipeline.create_job(conn, "invoice")
    pipeline.ingest_prepared(conn, job, FIELDS, engine="recorded-fixture")
    try:
        finalize.finalize(conn, job)
    except ValueError as e:
        assert "not APPROVED" in str(e), e
        print("          generation refused before human approval", flush=True)
        return
    raise AssertionError("generated a document from unapproved fields")


check("generation refused while a job is still NEEDS_REVIEW", t_refuse_unapproved)


# ── show the two chains side by side ──────────────────────────────────────
for label, job in (("CLEAN RUN", CLEAN.get("job")), ("CORRUPTED RUN", DIRTY.get("job"))):
    if not job:
        continue
    print(f"\n  {label}  ({job})")
    for e in audit.chain(conn, job):
        d = e["detail"]
        extra = ""
        if e["step"] == "self-verify":
            extra = f"  <-- {d['result']}"
            if d["result"] == "FAIL":
                m = d["mismatches"][0]
                extra += f" ({m['field']}: {m['approved']!r} != {m['found']!r})"
        elif e["step"] == "status":
            extra = f"  -> {d['status']}"
        print(f"    {e['seq']:>2}  {e['actor']:<6} {e['step']:<28}{extra}")

conn.close()
bad = [n for ok, n in results if not ok]
print(f"\n  {len(results)-len(bad)}/{len(results)} passed")
for n in bad:
    print(f"    FAILED: {n}")
raise SystemExit(1 if bad else 0)
