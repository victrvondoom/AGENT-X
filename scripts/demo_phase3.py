"""
Phase 3 deliverable -- drive one job end-to-end through a human correction.

Proves the four things the spec asks for, and proves the gate cannot be walked
around, which is the part that actually matters:

  1. only low-confidence fields are presented for review
  2. a human ACCEPT and a human CORRECT are both recorded, with both values
  3. the job resumes to APPROVED only when ZERO fields remain unreviewed
  4. the audit chain answers "who changed what and when" from its own rows

Usage:
    DATABASE_URL=postgresql://... python scripts/demo_phase3.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv                          # noqa: E402
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # pyright: ignore[reportAttributeAccessIssue] -- guarded by hasattr above

import psycopg                                          # noqa: E402
from core.trust import audit                            # noqa: E402
from pipelines.document import pipeline, review         # noqa: E402

DSN = os.environ.get("DATABASE_URL")
if not DSN:
    print("NOT IMPLEMENTED: DATABASE_URL is not set.")
    raise SystemExit(1)

RECORDED = [
    {"name": "invoice_number", "value": "INV-4471",       "confidence": 0.97, "recognition": 0.95},
    {"name": "vendor_name",    "value": "Acme Logistics", "confidence": 0.88, "recognition": 0.93},
    {"name": "date",           "value": "2026-07-14",     "confidence": 0.94, "recognition": 0.91},
    {"name": "total",          "value": "1,248.00",       "confidence": 0.91, "recognition": 0.90},
    {"name": "account_number", "value": "GB29 NWBK 6016", "confidence": 0.99, "recognition": 0.41},
    {"name": "description",    "value": "freight, Q3",    "confidence": None, "recognition": 0.86},
]

results: list[tuple[bool, str]] = []


def check(name, fn):
    try:
        fn()
        results.append((True, name)); print("  PASS  " + name, flush=True)
    except Exception as e:
        results.append((False, name))
        print(f"  FAIL  {name}\n          {type(e).__name__}: {e}", flush=True)


conn = psycopg.connect(DSN, autocommit=True, connect_timeout=10)
JOB = {}

# ── set the job up in NEEDS_REVIEW ────────────────────────────────────────
def t_setup():
    JOB["id"] = pipeline.create_job(conn, "invoice")
    r = pipeline.ingest_prepared(conn, JOB["id"], RECORDED, engine="recorded-fixture")
    assert r["status"] == "NEEDS_REVIEW", r
    print(f"          job {JOB['id']}  {r['summary']['auto']} auto, "
          f"{r['summary']['human']} held", flush=True)


check("job routed to NEEDS_REVIEW", t_setup)


# ── only the held fields are offered ──────────────────────────────────────
def t_only_held():
    p = review.pending(conn, JOB["id"])
    names = [f["name"] for f in p]
    assert "invoice_number" not in names, "an auto-accepted field was offered for review"
    assert "date" not in names, names
    assert set(names) == {"total", "account_number", "description"}, names
    # worst-first: absent confidence sorts to the top
    assert names[0] == "description", f"expected the unscored field first, got {names}"
    print(f"          offered (worst first): {names}", flush=True)


check("only low-confidence fields are offered, worst first", t_only_held)


# ── the pipeline REFUSES to advance while work is outstanding ─────────────
def t_cannot_skip():
    resumed = review._resume(conn, JOB["id"], "cheater")
    assert resumed is False, "the job advanced with fields still unreviewed"
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM jobs WHERE id = %s", (JOB["id"],))
        row = cur.fetchone()
        assert row is not None and row[0] == "NEEDS_REVIEW"
    print("          direct resume() call refused -- the guard is in SQL", flush=True)


check("cannot resume while fields remain unreviewed", t_cannot_skip)


# ── a human ACCEPTS one field ─────────────────────────────────────────────
def t_accept():
    f = [x for x in review.pending(conn, JOB["id"]) if x["name"] == "account_number"][0]
    out = review.decide(conn, JOB["id"], f["id"], "ACCEPT", "v.kumar")
    assert out["remaining"] == 2, out
    assert out["resumed"] is False
    print(f"          accepted account_number, {out['remaining']} left", flush=True)


check("human ACCEPT recorded, job still blocked", t_accept)


# ── the same field cannot be ruled on twice ───────────────────────────────
def t_no_double():
    with conn.cursor() as cur:
        cur.execute("SELECT id::text FROM fields WHERE job_id = %s AND name='account_number'",
                    (JOB["id"],))
        row = cur.fetchone()
        assert row is not None
        fid = row[0]
    try:
        review.decide(conn, JOB["id"], fid, "ACCEPT", "someone.else")
    except review.ReviewError as e:
        assert "already been reviewed" in str(e), e
        print("          second ruling refused", flush=True)
        return
    raise AssertionError("a field was reviewed twice")


check("a field cannot be ruled on twice", t_no_double)


# ── a human CORRECTS a field -- the real one ──────────────────────────────
def t_correct():
    f = [x for x in review.pending(conn, JOB["id"]) if x["name"] == "total"][0]
    assert f["value"] == "1,248.00"
    out = review.decide(conn, JOB["id"], f["id"], "CORRECT", "v.kumar",
                        new_value="12,480.00")     # the extractor dropped a digit
    assert out["final_value"] == "12,480.00", out
    assert out["remaining"] == 1, out
    print(f"          corrected total 1,248.00 -> 12,480.00, {out['remaining']} left",
          flush=True)


check("human CORRECT recorded with both values", t_correct)


# ── the last ruling resumes the pipeline ──────────────────────────────────
def t_resume():
    f = review.pending(conn, JOB["id"])[0]
    out = review.decide(conn, JOB["id"], f["id"], "ACCEPT", "a.reviewer")
    assert out["remaining"] == 0, out
    assert out["resumed"] is True, "the last ruling did not resume the pipeline"
    p = review.progress(conn, JOB["id"])
    assert p["status"] == "APPROVED", p
    assert p["corrected"] == 1, p
    print(f"          last ruling -> APPROVED ({p['corrected']} field corrected)", flush=True)


check("zero remaining -> job resumes to APPROVED", t_resume)


# ── review is refused once the job has moved on ───────────────────────────
def t_closed():
    with conn.cursor() as cur:
        cur.execute("SELECT id::text FROM fields WHERE job_id=%s LIMIT 1", (JOB["id"],))
        row = cur.fetchone()
        assert row is not None
        fid = row[0]
    try:
        review.decide(conn, JOB["id"], fid, "ACCEPT", "late.reviewer")
    except review.ReviewError as e:
        assert "not NEEDS_REVIEW" in str(e), e
        print("          post-approval review refused", flush=True)
        return
    raise AssertionError("review accepted after approval")


check("review refused once the job is APPROVED", t_closed)


# ── the chain answers who/what/when on its own ────────────────────────────
def t_chain():
    ch = audit.chain(conn, JOB["id"])
    v = audit.verify_chain(conn, JOB["id"])
    assert v["ok"], v
    reviews = [e for e in ch if e["step"] == "review"]
    assert len(reviews) == 3, f"expected 3 human rulings, got {len(reviews)}"
    corr = [e for e in reviews if e["detail"].get("changed")]
    assert len(corr) == 1, corr
    d = corr[0]["detail"]
    assert d["machine_value"] == "1,248.00" and d["final_value"] == "12,480.00", d
    assert d["reviewer"] == "v.kumar", d
    print(f"          {v['rows']} rows, {v['reason']}", flush=True)


check("audit chain records who changed what, and verifies", t_chain)


# ── show it ───────────────────────────────────────────────────────────────
print("\n  AUDIT CHAIN")
for e in audit.chain(conn, JOB["id"]):
    d = e["detail"]
    if e["step"] == "review":
        what = (f"review {d['field']} · {d['action']} · by {d['reviewer']}"
                + (f" · \"{d['machine_value']}\" -> \"{d['final_value']}\"" if d.get("changed") else ""))
    elif e["step"] == "status":
        what = f"status -> {d['status']}"
    else:
        what = e["step"]
    print(f"    {e['seq']:>2}  {e['actor']:<6} {what}")
    print(f"        {e['ts']}  {e['content_hash'][:24]}...")

print("\n  FINAL FIELDS")
for f in pipeline.job_fields(conn, JOB["id"]):
    who = f["reviewed_by"] or "-"
    print(f"    {f['name']:<16} {str(f['value']):<16} {f['decision']:<6} {who}")

conn.close()
bad = [n for ok, n in results if not ok]
print(f"\n  {len(results)-len(bad)}/{len(results)} passed")
for n in bad:
    print(f"    FAILED: {n}")
raise SystemExit(1 if bad else 0)
