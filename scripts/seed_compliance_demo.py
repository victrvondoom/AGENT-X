"""
The compliance demo — the scenario the flat-table competitors structurally cannot handle.

Two customers, Alice and Bob, both employed at the SAME company (Acme Corp) — a *shared*
entity in the knowledge graph. Alice exercises her GDPR Article 17 right to be forgotten.
Agent X erases Alice's exclusive knowledge (her mortgage, her provenance) AND crypto-shreds
her key, while *provably retaining* Acme Corp for Bob — with the shared node's Alice-provenance
removed. A competitor whose schema is flat per-subject tables cannot express this: it can only
destroy "Alice", with no notion of an entity shared with a surviving subject.

Load it into the 'compliance' workspace:  python scripts/seed_compliance_demo.py
Then in the app, switch to the 'compliance' workspace and Forget & Prove `alice-chen`.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ingest import ingest_document   # noqa: E402
from db import store                       # noqa: E402

WS = "compliance"

CORPUS = [
    ("alice-chen", "Alice Chen — customer record",
     "Alice Chen is a customer employed at Acme Corp. She holds a mortgage account MTG-1001 with "
     "Riverside Bank and lists Acme Corp as her employer for income verification. Her relationship "
     "manager is Dana Lee."),
    ("bob-martinez", "Bob Martinez — customer record",
     "Bob Martinez is a customer, also employed at Acme Corp. He holds a savings account SAV-2002 "
     "with Riverside Bank and lists Acme Corp as his employer. His relationship manager is Dana Lee."),
    ("carol-singh", "Carol Singh — customer record",
     "Carol Singh is a customer employed at Globex Ltd, unrelated to Acme Corp. She holds a business "
     "checking account BUS-3003 with Riverside Bank."),
]


def main():
    print(f"Seeding the compliance demo into workspace '{WS}'…")
    with store.connect() as conn, conn.cursor() as c:
        for t in ("documents", "nodes", "edges", "erasure_events", "timeline"):
            c.execute(f"DELETE FROM {t} WHERE workspace = %s", (WS,))
        c.execute("DELETE FROM subject_keys WHERE workspace = %s", (WS,))

    for subj, title, text in CORPUS:
        r = ingest_document(subj, title, text, WS)
        print(f"  ingested {subj:14s} -> {r.get('entities', r)} entities")

    # Show the shared entity so it's obvious what the demo proves
    with store.connect() as conn, conn.cursor() as c:
        c.execute(
            "SELECT name, subjects FROM nodes WHERE workspace = %s AND array_length(subjects, 1) > 1 "
            "ORDER BY name", (WS,))
        shared = c.fetchall()
    print("\nShared entities (belong to >1 subject — these must survive a single-subject forget):")
    for name, subjects in shared:
        print(f"  • {name}  ← {subjects}")
    print(f"\nDone. In the app, switch to the '{WS}' workspace and Forget & Prove 'alice-chen' —")
    print("watch Acme Corp / Riverside Bank / Dana Lee stay for Bob, with Alice's provenance removed.")


if __name__ == "__main__":
    main()
