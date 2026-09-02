"""Audit-ledger hash-chain tests.

The Audit Ledger claims tamper-evidence: altering any historical entry must
invalidate every hash after it. The frontend re-derives the same chain
client-side to verify it independently, so the two payload formats must
agree exactly - if they drift, "verify chain" silently starts failing on
honest data.
"""

from __future__ import annotations

import hashlib

from app.ledger import GENESIS as _LEDGER_GENESIS
from app.ledger import chain_hash as _sha256_chain_hash
from app.ledger import ledger_payload as _ledger_payload
from app.ledger import build_chain, verify_chain


def _build_chain(events: list[tuple[str, str, str, str, str]]) -> list[dict]:
    """(ts, finding_id, agent, action, detail) -> chained entries."""
    chain: list[dict] = []
    prev = _LEDGER_GENESIS
    for seq, (ts, fid, agent, action, detail) in enumerate(events):
        h = _sha256_chain_hash(prev, _ledger_payload(fid, agent, action, detail, ts))
        chain.append({"seq": seq, "hash": h, "prevHash": prev, "findingId": fid,
                      "agent": agent, "action": action, "detail": detail, "timestamp": ts})
        prev = h
    return chain


EVENTS = [
    ("2026-01-01T00:00:00Z", "F-1", "hunter", "ingestion verified", "detected"),
    ("2026-01-01T00:01:00Z", "F-1", "analyst", "pipeline event", "confirmed"),
    ("2026-01-01T00:02:00Z", "F-1", "patch-forge", "pipeline event", "patched"),
]


def _verify(chain: list[dict]) -> bool:
    prev = chain[0]["prevHash"] if chain else _LEDGER_GENESIS
    for e in chain:
        expected = _sha256_chain_hash(
            prev, _ledger_payload(e["findingId"], e["agent"], e["action"], e["detail"], e["timestamp"])
        )
        if e["hash"] != expected or e["prevHash"] != prev:
            return False
        prev = e["hash"]
    return True


def test_intact_chain_verifies():
    assert _verify(_build_chain(EVENTS)) is True


def test_chain_starts_from_genesis():
    assert _build_chain(EVENTS)[0]["prevHash"] == _LEDGER_GENESIS


def test_each_entry_links_to_its_predecessor():
    chain = _build_chain(EVENTS)
    for prev_entry, entry in zip(chain, chain[1:]):
        assert entry["prevHash"] == prev_entry["hash"]


def test_altering_a_historical_entry_breaks_verification():
    chain = _build_chain(EVENTS)
    chain[1]["detail"] = "quietly rewritten"
    assert _verify(chain) is False


def test_deleting_an_entry_breaks_the_chain():
    chain = _build_chain(EVENTS)
    del chain[1]
    assert _verify(chain) is False


def test_reordering_entries_breaks_the_chain():
    chain = _build_chain(EVENTS)
    chain[1], chain[2] = chain[2], chain[1]
    assert _verify(chain) is False


def test_payload_format_matches_the_frontend_contract():
    """lib/sentinel/api.ts ledgerEntryPayload() builds this exact string.
    If this changes without the frontend changing, client-side chain
    verification breaks on data that is actually fine."""
    assert _ledger_payload("F-1", "hunter", "act", "det", "TS") == "F-1|hunter|act|det|TS"


def test_chain_hash_is_sha256_of_prev_plus_payload():
    payload = _ledger_payload("F-1", "hunter", "act", "det", "TS")
    expected = hashlib.sha256((_LEDGER_GENESIS + payload).encode()).hexdigest()
    assert _sha256_chain_hash(_LEDGER_GENESIS, payload) == f"sha256:{expected}"
