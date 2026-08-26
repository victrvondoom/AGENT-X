"""
Phase 7 -- where the two pipelines collide.

The conflict: an audit chain must be immutable or it proves nothing; erasure must
destroy personal data; the chain CONTAINS personal data; and deleting a hash-chained
row destroys every hash after it.

The claim: seal the sensitive half under the subject's key and hash the ciphertext.
Destroy the key and the chain STILL VERIFIES -- it still proves what happened and
when -- while the personal data is cryptographically unrecoverable.

This script proves the claim rather than asserting it: it verifies the chain, shreds
the key, verifies again, and shows the same rows now reading as tombstones.

    DATABASE_URL=postgresql://... python scripts/demo_phase7.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv                            # noqa: E402
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # pyright: ignore[reportAttributeAccessIssue] -- guarded by hasattr above

import contextlib, psycopg                                # noqa: E402

DSN = os.environ.get("DATABASE_URL")
if not DSN:
    print("NOT IMPLEMENTED: DATABASE_URL is not set.")
    raise SystemExit(1)
os.environ.setdefault("AGENT_X_ROOT_KEY", "")

from db import store                                      # noqa: E402


@contextlib.contextmanager
def _direct():
    assert DSN is not None, "checked at module load — see the SystemExit above"
    c = psycopg.connect(DSN, autocommit=True, connect_timeout=10)
    try:
        yield c
    finally:
        c.close()


store.connect = _direct

from core.trust import audit, sealed                      # noqa: E402
from core.trust import pipeline_job as pj                 # noqa: E402
from pipelines.document import pipeline, review           # noqa: E402
import core.forget as F                                   # noqa: E402

if not os.environ.get("AGENT_X_ROOT_KEY"):
    os.environ["AGENT_X_ROOT_KEY"] = store.generate_root_key()

# A fresh workspace per run. Crypto-shredding is IRREVERSIBLE by design, so a
# re-run against the same subject would find a destroyed key and prove nothing --
# the demo must not depend on being the first one ever executed.
import uuid as _uuid
WS = "phase7-" + _uuid.uuid4().hex[:8]
SUBJ = "alice-mercer"
results: list[tuple[bool, str]] = []


def check(name, fn):
    try:
        fn(); results.append((True, name)); print("  PASS  " + name, flush=True)
    except Exception as e:
        results.append((False, name))
        print(f"  FAIL  {name}\n          {type(e).__name__}: {e}", flush=True)


conn = psycopg.connect(DSN, autocommit=True, connect_timeout=10)
S = {}


# ── a document job about a real person, with sealed audit detail ──────────
def t_seal():
    job = pipeline.create_job(conn, "invoice", WS)
    with conn.cursor() as cur:
        cur.execute("UPDATE jobs SET subject = %s WHERE id = %s", (SUBJ, job))
    pipeline.ingest_prepared(conn, job, [
        {"name": "invoice_number", "value": "INV-4471",   "confidence": 0.97, "recognition": 0.95},
        {"name": "account_number", "value": "GB29 NWBK 6016 1331 9268 19",
         "confidence": 0.99, "recognition": 0.41},
    ], engine="recorded-fixture")

    f = [x for x in review.pending(conn, job) if x["name"] == "account_number"][0]
    review.decide(conn, job, f["id"], "CORRECT", "v.kumar",
                  "GB29 NWBK 6016 1331 9268 20")

    # the personal detail goes on the chain SEALED under alice's key
    sealed.append_sealed(conn, job, "review.detail", "HUMAN", {
        "field": "account_number", "action": "CORRECT",
        "reviewer": "v.kumar",
        "machine_value": "GB29 NWBK 6016 1331 9268 19",
        "final_value": "GB29 NWBK 6016 1331 9268 20",
        "subject_name": "Alice Mercer",
    }, subject=SUBJ, workspace=WS)

    S["job"] = job
    ch = sealed.readable_chain(conn, job)
    row = [r for r in ch if r["step"] == "review.detail"][0]
    assert row["sealed"] is True
    assert row["detail"]["machine_value"].endswith("19"), row["detail"]
    # Capture every row hash, not just the head. Appending legitimately MOVES the
    # head, so "the head is unchanged" is the wrong invariant -- the right one is
    # that existing history is a prefix of later history: extended, never rewritten.
    S["prefix_before"] = [(e["seq"], e["content_hash"]) for e in audit.chain(conn, job)]
    print(f"          sealed row readable: reviewer={row['detail']['reviewer']}, "
          f"value ...{row['detail']['final_value'][-2:]}", flush=True)


check("sealed audit detail is readable while the key exists", t_seal)


def t_verify_before():
    v = audit.verify_chain(conn, S["job"])
    assert v["ok"], v
    print(f"          {v['rows']} rows, {v['reason']}", flush=True)


check("chain verifies BEFORE erasure", t_verify_before)


# ── now erase the subject ─────────────────────────────────────────────────
def t_erase():
    # give the subject something in the memory graph so forget() has work to do
    with store.connect() as c:
        blob = store.encrypt_for(c, WS, SUBJ, "alice denial letter")
        with c.cursor() as cur:
            cur.execute("INSERT INTO documents (workspace,subject,title,content_enc,source_kind) "
                        "VALUES (%s,%s,'denial.pdf',%s,'user_evidence')", (WS, SUBJ, blob))
    r = F.forget(SUBJ, WS)
    S["erasure_job"] = r.get("job_id")
    red = sealed.redact_subject(conn, WS, SUBJ, S["erasure_job"])
    S["red"] = red
    assert red["document_jobs"] >= 1, red
    print(f"          erased; redacted {red['fields_redacted']} field(s) across "
          f"{red['document_jobs']} compliance job(s)", flush=True)


check("erase the subject, redacting their document evidence", t_erase)


# ── THE CLAIM ─────────────────────────────────────────────────────────────
def t_verify_after():
    """The whole point: destroying the key must not break the proof."""
    v = audit.verify_chain(conn, S["job"])
    assert v["ok"], f"the chain BROKE after crypto-shred: {v}"
    after = [(e["seq"], e["content_hash"]) for e in audit.chain(conn, S["job"])]
    before = S["prefix_before"]
    assert after[:len(before)] == before, (
        "pre-erasure history changed -- shredding rewrote the chain instead of "
        "leaving the ciphertext alone")
    print(f"          chain STILL verifies: {v['rows']} rows; all {len(before)} "
          f"pre-erasure hashes unchanged", flush=True)


check("chain STILL verifies AFTER the key is destroyed", t_verify_after)


def t_data_gone():
    ch = sealed.readable_chain(conn, S["job"])
    row = [r for r in ch if r["step"] == "review.detail"][0]
    d = row["detail"]
    assert row["shredded"] is True, d
    assert "machine_value" not in d and "subject_name" not in d, d
    # the SHAPE of the record survives -- a regulator can still read what happened
    assert d.get("field") == "account_number" and d.get("action") == "CORRECT", d
    assert d.get("reviewer") is None, "reviewer identity should be sealed"
    print(f"          detail now: {d.get('_shredded')}", flush=True)
    print(f"          but still legible: field={d.get('field')} action={d.get('action')}",
          flush=True)


check("the personal values are unrecoverable, the record's shape survives", t_data_gone)


def t_fields_redacted():
    with conn.cursor() as cur:
        cur.execute("SELECT name, value, redacted_at IS NOT NULL FROM fields "
                    "WHERE job_id = %s ORDER BY name", (S["job"],))
        rows = cur.fetchall()
    assert all(r[1] is None and r[2] for r in rows), rows
    print(f"          {len(rows)} field values nulled, redaction timestamped", flush=True)


check("document field values are redacted, the job is retained", t_fields_redacted)


def t_record_retained():
    """The compliance record itself must SURVIVE -- it is the evidence of lawful handling."""
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM jobs WHERE id = %s", (S["job"],))
        row = cur.fetchone()
        assert row is not None and row[0] == 1, "the compliance job was destroyed"
    ch = audit.chain(conn, S["job"])
    assert any(e["step"] == "redacted" for e in ch), "the redaction was not audited"
    print(f"          job retained, {len(ch)} chain rows, redaction recorded", flush=True)


check("the compliance record survives the erasure", t_record_retained)


def t_no_resurrect():
    """Re-sealing after a shred must not re-create the data."""
    out = sealed.append_sealed(conn, S["job"], "late.write", "AGENT",
                               {"secret": "should not persist"},
                               subject=SUBJ, workspace=WS)
    assert out["sealed"] is False, "data was re-sealed under a destroyed key"
    ch = sealed.readable_chain(conn, S["job"])
    row = [r for r in ch if r["step"] == "late.write"][0]
    assert "secret" not in row["detail"], row["detail"]
    assert row["detail"].get("sealed_omitted"), row["detail"]
    print("          post-shred write refused to persist the value", flush=True)


check("a write after the shred cannot resurrect the subject", t_no_resurrect)


# ── show it ───────────────────────────────────────────────────────────────
print("\n  THE CHAIN, AFTER ERASURE")
for r in sealed.readable_chain(conn, S["job"]):
    mark = "  [shredded]" if r["shredded"] else ""
    print(f"    {r['seq']:>2}  {r['actor']:<6} {r['step']:<16} {r['content_hash'][:16]}...{mark}")
v = audit.verify_chain(conn, S["job"])
print(f"\n  verification: {v['rows']} rows -- {v['reason']}")
print("  the proof survived; the personal data did not.")

conn.close()
bad = [n for ok, n in results if not ok]
print(f"\n  {len(results)-len(bad)}/{len(results)} passed")
for n in bad:
    print(f"    FAILED: {n}")
raise SystemExit(1 if bad else 0)
