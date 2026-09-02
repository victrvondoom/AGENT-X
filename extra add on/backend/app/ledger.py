"""Audit-ledger hash-chain primitives.

Deliberately dependency-free (stdlib only): this is the tamper-evidence
mechanism the Audit Ledger's "verify chain" claim rests on, so it must be
verifiable in isolation without standing up the web layer, the vector
store, or any cloud client.

The payload format below is a cross-boundary contract. The frontend's
``ledgerEntryPayload()`` in ``src/lib/sentinel/api.ts`` builds the same
string and re-hashes the chain client-side, which is what makes the ledger
*independently* verifiable rather than merely displayed. If one side's
format changes without the other, in-browser verification starts failing
on data that is actually intact - so both sides are pinned by tests.
"""

from __future__ import annotations

import hashlib

GENESIS = "sha256:genesis"


def ledger_payload(finding_id: str, agent: str, action: str, detail: str, ts: str) -> str:
    """Canonical per-entry payload. Must stay byte-identical to the
    frontend's ledgerEntryPayload()."""
    return f"{finding_id}|{agent}|{action}|{detail}|{ts}"


def chain_hash(prev_hash: str, payload: str) -> str:
    """SHA-256 over (previous hash + this entry's payload). Chaining the
    predecessor's digest is what makes altering, deleting, or reordering
    any historical entry invalidate every hash after it."""
    digest = hashlib.sha256((prev_hash + payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def build_chain(events: list[dict]) -> list[dict]:
    """Chains pre-sorted events. Each event needs findingId, title, agent,
    action, detail, timestamp."""
    chain: list[dict] = []
    prev = GENESIS
    for seq, e in enumerate(events):
        entry_hash = chain_hash(
            prev, ledger_payload(e["findingId"], e["agent"], e["action"], e["detail"], e["timestamp"])
        )
        chain.append({**e, "seq": seq, "hash": entry_hash, "prevHash": prev})
        prev = entry_hash
    return chain


def verify_chain(chain: list[dict]) -> tuple[bool, int | None]:
    """Re-derives every hash from its predecessor. Returns (ok, seq of the
    first broken entry)."""
    prev = chain[0]["prevHash"] if chain else GENESIS
    for entry in chain:
        expected = chain_hash(
            prev,
            ledger_payload(
                entry["findingId"], entry["agent"], entry["action"], entry["detail"], entry["timestamp"]
            ),
        )
        if entry["hash"] != expected or entry["prevHash"] != prev:
            return False, entry.get("seq")
        prev = entry["hash"]
    return True, None
