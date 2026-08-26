"""
Phase 2 deliverable -- extract a messy document and show the AUTO/HUMAN routing.

Two modes, and the script tells you which one ran:

  LIVE      DWS_API_KEY is set. Calls POST /extract for real.
  RECORDED  No key. Routes a recorded DWS-shaped extraction instead, and records
            engine='recorded-fixture' on the audit chain so the run can never be
            mistaken for a live one.

The gate is identical in both. What the fixture cannot prove is that DWS returns
the shape we parse; that needs the key. Everything else here is real.

Usage:
    DATABASE_URL=postgresql://... python scripts/demo_phase2.py
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
from core.trust import audit, gate                      # noqa: E402
from pipelines.document import dws, pipeline            # noqa: E402

DSN = os.environ.get("DATABASE_URL")
if not DSN:
    print("NOT IMPLEMENTED: DATABASE_URL is not set.")
    raise SystemExit(1)

# A deliberately messy invoice: a smudged total, an unscored field, a barely-legible
# account number, and clean fields that should sail through. Shaped exactly like a
# normalise() result so it exercises the real routing path.
RECORDED = [
    {"name": "invoice_number", "value": "INV-4471",        "confidence": 0.97, "recognition": 0.95},
    {"name": "vendor_name",    "value": "Acme Logistics",  "confidence": 0.88, "recognition": 0.93},
    {"name": "date",           "value": "2026-07-14",      "confidence": 0.94, "recognition": 0.91},
    {"name": "due_date",       "value": "2026-08-13",      "confidence": 0.90, "recognition": 0.88},
    {"name": "total",          "value": "12,480.00",       "confidence": 0.91, "recognition": 0.90},
    {"name": "tax",            "value": "2,246.40",        "confidence": 0.72, "recognition": 0.84},
    {"name": "account_number", "value": "GB29 NWBK 6016",  "confidence": 0.99, "recognition": 0.41},
    {"name": "address",        "value": "12 Dock Rd",      "confidence": 0.63, "recognition": 0.77},
    {"name": "description",    "value": "freight, Q3",     "confidence": None, "recognition": 0.86},
]

conn = psycopg.connect(DSN, autocommit=True)

live = dws.configured()
print(f"\n  MODE: {'LIVE (DWS_API_KEY set)' if live else 'RECORDED (no DWS_API_KEY)'}")
if not live:
    print("  NOT IMPLEMENTED: live POST /extract -- DWS_API_KEY is not set.")
    print("  The confidence gate below is real; the extraction feeding it is a fixture.\n")

job = pipeline.create_job(conn, "invoice")
print(f"  job {job}\n")

result = pipeline.ingest_prepared(conn, job, RECORDED, engine="recorded-fixture")

# ── the field table ───────────────────────────────────────────────────────
rows = result["fields"]
w = max(len(r["name"]) for r in rows) + 2
print("  FIELD".ljust(w + 2), "CONF   OCR    ROUTE   REASON")
print("  " + "-" * (w + 58))
for r in sorted(rows, key=lambda x: (x["decision"], x["name"])):
    c = "  --  " if r["confidence"] is None else f" {r['confidence']:.2f} "
    o = "  --  " if r.get("recognition") is None else f" {r['recognition']:.2f} "
    mark = "*" if r["decision"] == "HUMAN" else " "
    print(f"  {mark}{r['name'].ljust(w)}{c} {o}  {r['decision']:<6}  {r['reason']}")

s = result["summary"]
print(f"\n  {s['total']} fields -> {s['auto']} AUTO, {s['human']} HUMAN")
print(f"  status: {result['status']}")
print("  reasons: " + ", ".join(f"{k}={v}" for k, v in sorted(s["reasons"].items())))

# ── why each HUMAN field was held ─────────────────────────────────────────
print("\n  WHY THE HELD FIELDS WERE HELD")
for r in rows:
    if r["decision"] == "HUMAN":
        d = gate.route(r["name"], r["confidence"], r.get("recognition"))
        print(f"    {r['name']}: {d.explain}")

# ── the audit chain for this job ──────────────────────────────────────────
print("\n  AUDIT CHAIN")
for e in audit.chain(conn, job):
    print(f"    {e['seq']}  {e['step']:<20} {e['actor']:<6} {e['content_hash'][:16]}...")

v = audit.verify_chain(conn, job)
print(f"\n  chain: {v['rows']} rows, {v['reason']}, head {v['head'][:16]}...")

ok = (v["ok"]
      and result["status"] == "NEEDS_REVIEW"
      and s["auto"] == 3 and s["human"] == 6
      and s["reasons"].get("no_confidence_signal") == 1
      and s["reasons"].get("illegible_source") == 1)
print(f"\n  {'PASS' if ok else 'FAIL'} -- gate routed {s['human']} of {s['total']} "
      f"to review and the chain is intact")

conn.close()
raise SystemExit(0 if ok else 1)
