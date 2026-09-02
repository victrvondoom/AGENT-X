"""evidence_agent_seal_record() must actually persist to the configured
EvidenceStore, not just write a local JSON file.

put_evidence() was defined on the abstract base and implemented three times
(local, Firestore, DynamoDB) - and never called anywhere in the running
application. assemble_evidence() only ever wrote EVIDENCE_DIR/<id>.json.
Every real investigation, through any orchestrator, on any store backend,
was landing on whatever machine's disk ran the worker; the configured cloud
store was never actually written to. Live on Firestore, this meant every
"successful" investigation left the record showing whatever had been
manually seeded once, forever - discovered when five real investigations in
a row completed with a genuine verdict and RESOLVED status, while Firestore's
own update_time stayed frozen a full day in the past.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app import agent_tools


def test_seal_record_persists_to_the_configured_store(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_tools, "EVIDENCE_DIR", tmp_path, raising=False)

    finding = agent_tools.Finding(
        finding_id="SENTINEL-F-TEST",
        severity="high",
        component="x",
        version="1",
        source="npm audit",
        advisory_id="GHSA-test",
    )
    monkeypatch.setattr(agent_tools, "_find_finding", lambda fid: finding)

    store = MagicMock()
    monkeypatch.setattr(agent_tools, "get_store", lambda: store)

    # assemble_evidence writes a real file via EVIDENCE_DIR inside
    # evidence_agent.py, not agent_tools.py - patch it directly so this test
    # doesn't depend on that module's own module-level constant.
    import app.agents.evidence_agent as ea

    monkeypatch.setattr(ea, "EVIDENCE_DIR", tmp_path)
    monkeypatch.setattr(ea, "_maybe_dws_seal", lambda *a, **k: None)

    result = agent_tools.evidence_agent_seal_record("SENTINEL-F-TEST")

    store.put_evidence.assert_called_once()
    called_id, called_payload = store.put_evidence.call_args[0]
    assert called_id == "SENTINEL-F-TEST"
    assert called_payload == result
    assert called_payload["finding_id"] == "SENTINEL-F-TEST"
