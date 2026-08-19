"""
Agent X evaluation harness.

Two measurements, both run live against CockroachDB:

1. Reconstruction-Robustness Score (RRS) — the security claim.
   An attacker holds a leaked/backup copy of a record's ciphertext (as lingers in MVCC history or
   backups) and tries to recover the plaintext after the record is "deleted".
   - Naive delete: rows removed but the encryption key survives -> leaked ciphertext still
     decryptable -> recovered.
   - Agent X: the forget transaction crypto-shreds the key -> leaked ciphertext is
     cryptographically unrecoverable -> not recovered.
   Mirrors Ghost Vectors (arXiv:2606.18497): API-deletion leaves data ~99% recoverable; key
   destruction drops it to 0%.

2. Forget-correctness — the behavioral claim.
   Ingest several people, hard-forget half, and verify the forgotten people's identifying data no
   longer surfaces in answers while survivors' data still does.

Usage:  python evals/rrs.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _alnum(s: str) -> str:
    """Normalize for robust matching (ignores unicode hyphens, spacing, case)."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())

from core.ingest import ingest_document   # noqa: E402
from core.ask import ask                   # noqa: E402
from core.forget import forget             # noqa: E402
from db import store                       # noqa: E402

# (subject_id, person, account, document text)
CORPUS = [
    ("cust-01", "Dana Lee", "DL-9001", "Dana Lee holds account DL-9001 at Riverside Bank with a gold savings plan."),
    ("cust-02", "Omar Farid", "OF-9002", "Omar Farid holds account OF-9002 at Riverside Bank with a platinum credit card."),
    ("cust-03", "Mei Chen", "MC-9003", "Mei Chen holds account MC-9003 at Riverside Bank with a business checking account."),
    ("cust-04", "Sara Kohl", "SK-9004", "Sara Kohl holds account SK-9004 at Riverside Bank with a student savings account."),
    ("cust-05", "Tom Rivera", "TR-9005", "Tom Rivera holds account TR-9005 at Riverside Bank with a mortgage loan."),
    ("cust-06", "Priya Nair", "PN-9006", "Priya Nair holds account PN-9006 at Riverside Bank with a joint checking account."),
]


# The eval runs in its OWN isolated workspace so it never touches demo/production data.
WS = "__eval__"


def _reset():
    # Scoped to the eval workspace ONLY — never a bare DELETE across all workspaces.
    with store.connect() as conn, conn.cursor() as c:
        for t in ("edges", "nodes", "documents", "subject_keys", "timeline", "erasure_events"):
            c.execute(f"DELETE FROM {t} WHERE workspace = %s", (WS,))


def _ingest_all():
    for s, person, acct, txt in CORPUS:
        ingest_document(s, person, txt, WS)
    blobs = {}
    with store.connect() as conn, conn.cursor() as c:
        for s, *_ in CORPUS:
            c.execute("SELECT content_enc FROM documents WHERE subject = %s AND workspace = %s LIMIT 1", (s, WS))
            row = c.fetchone()
            blobs[s] = row[0] if row else None
    return blobs


def _naive_delete(subject):
    # Typical implementation: delete rows, but forget to destroy the key (the fatal gap).
    with store.connect() as conn, conn.cursor() as c:
        c.execute("DELETE FROM documents WHERE subject = %s AND workspace = %s", (subject, WS))
        c.execute("DELETE FROM nodes WHERE %s::STRING = ANY(subjects) AND workspace = %s", (subject, WS))


def _recovered(subject, blob) -> bool:
    with store.connect() as conn:
        return store.decrypt_for(conn, WS, subject, blob) is not None


def rrs():
    print("=" * 66)
    print("1. Reconstruction-Robustness Score (leaked-ciphertext attack)")
    n = len(CORPUS)

    _reset()
    blobs = _ingest_all()
    for s, *_ in CORPUS:
        _naive_delete(s)
    naive_rec = sum(1 for s, *_ in CORPUS if _recovered(s, blobs[s]))

    _reset()
    blobs = _ingest_all()
    for s, *_ in CORPUS:
        forget(s, WS)
    agentx_rec = sum(1 for s, *_ in CORPUS if _recovered(s, blobs[s]))

    print(f"   Naive delete    : {naive_rec}/{n} recovered  ->  RRS {100*(1-naive_rec/n):3.0f}%")
    print(f"   Agent X shred : {agentx_rec}/{n} recovered  ->  RRS {100*(1-agentx_rec/n):3.0f}%")
    return {"naive_recovered": naive_rec, "agent_x_recovered": agentx_rec, "n": n}


def correctness():
    print("=" * 66)
    print("2. Forget-correctness (behavioral: identifying data must not resurface)")
    _reset()
    for s, person, acct, txt in CORPUS:
        ingest_document(s, person, txt, WS)

    forgotten = CORPUS[:3]
    survivors = CORPUS[3:]
    for s, *_ in forgotten:
        forget(s, WS)

    def account_surfaces(person, acct):
        a = ask(f"What is {person}'s bank account number at Riverside Bank?", None, WS)
        return _alnum(acct) in _alnum(a)

    gone = sum(1 for _, person, acct, _ in forgotten if not account_surfaces(person, acct))
    intact = sum(1 for _, person, acct, _ in survivors if account_surfaces(person, acct))
    print(f"   Forgotten people — account no longer surfaces : {gone}/{len(forgotten)}")
    print(f"   Surviving people — account still answered     : {intact}/{len(survivors)}")
    return {"forgotten_gone": gone, "survivors_intact": intact}


if __name__ == "__main__":
    r1 = rrs()
    r2 = correctness()
    print("=" * 66)
    print("SUMMARY:", {**r1, **r2})
