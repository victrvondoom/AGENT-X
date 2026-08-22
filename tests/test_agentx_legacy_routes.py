"""
The legacy consumer routes, now backed by the real engine.

`/api/inspect_booking`, `/api/classify_intent` and `/api/cases` predate
`agentx/` and were served by keyword-heuristic prototypes in `core/`. Those
prototypes are no longer imported by any route: keeping them alive meant two
case systems in one codebase, the weaker one reachable at `/api/cases` with no
audit chain, no receipt and no crypto-shred — which contradicted the app's own
"one trust spine" claim.

The paths and response shapes are preserved because the existing console UI
calls them. What these tests pin is that the shapes are now produced by the
real engine, and that the prototype's worst behaviour is gone.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    os.environ.setdefault("DATABASE_URL", "postgresql://localhost:5432/agentx")
    os.environ["AGENT_X_ENGINE"] = "sqlite"
    db = tmp_path_factory.mktemp("legacy") / "legacy.db"
    os.environ["AGENT_X_DB_PATH"] = str(db)
    from agentx import store
    store.use_sqlite(str(db))
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


@pytest.fixture
def auth():
    tok = os.environ.get("AGENT_X_AUTH_TOKEN")
    return {"Authorization": f"Bearer {tok}"} if tok else {}


class TestInspectBooking:
    def test_an_ordinary_receipt_is_not_dispute_eligible(self, client):
        """The prototype appended a filler 'anomaly' whenever it found none, so
        `dispute_eligible` was unconditionally true and it recommended a
        chargeback for a coffee receipt. Eligibility now means an actually
        applicable right."""
        r = client.post("/api/inspect_booking",
                        json={"text": "Coffee shop receipt\nTotal: 3.40 GBP\nThank you"})
        assert r.status_code == 200
        audit = r.json()["audit"]
        assert audit["dispute_eligible"] is False
        assert audit["anomalies_detected"] == []
        assert r.json()["dispute_letter"] is None

    def test_a_real_problem_is_eligible_and_cites_a_right(self, client):
        r = client.post("/api/inspect_booking", json={
            "text": "Kartly charged me twice for the same order, 12.00 GBP each. "
                    "Order 402-9938271"})
        audit = r.json()["audit"]
        assert audit["problem_type"] == "duplicate_charge"
        assert audit["dispute_eligible"] is True
        assert audit["consumer_rights_advisory"], "an eligible case must cite a right"

    def test_response_keeps_the_shape_the_console_expects(self, client):
        r = client.post("/api/inspect_booking", json={"text": "Total: 5.00 GBP"})
        body = r.json()
        assert set(body) >= {"audit", "dispute_letter"}
        assert set(body["audit"]) >= {"merchant_category", "booking_ref",
                                      "anomalies_detected",
                                      "consumer_rights_advisory",
                                      "dispute_eligible", "recommended_action"}


class TestClassifyIntent:
    def test_ambiguity_is_reported_rather_than_collapsed(self, client):
        r = client.post("/api/classify_intent", json={"query": "They charged me again"})
        d = r.json()
        assert d["ambiguous"] is True
        assert len(d["alternatives"]) >= 2

    def test_shape_is_preserved(self, client):
        r = client.post("/api/classify_intent", json={"query": "my flight was delayed"})
        assert set(r.json()) >= {"capability", "domain", "problem_type",
                                 "requires_evidence", "autonomy_level"}


class TestLegacyCasesAreRealCases:
    def test_creating_through_the_legacy_route_produces_a_chained_case(
            self, client, auth):
        """The whole point of retiring the shadow system: a case opened at the
        old path is now indistinguishable from one opened at the new one —
        chained, sealed, receipt-able."""
        r = client.post("/api/cases", headers=auth, json={
            "title": "t", "domain": "finance", "problem_type": "duplicate_charge",
            "user_claim": "Kartly charged me twice for order 402-9938271, "
                          "12.00 GBP each", "evidence_text": ""})
        assert r.status_code == 200
        cid = r.json()["case_id"]
        assert cid.startswith("PX-")

        got = client.get(f"/api/agentx/cases/{cid}")
        assert got.status_code == 200
        assert got.json()["chain"]["length"] >= 1

    def test_legacy_list_returns_real_cases(self, client, auth):
        client.post("/api/cases", headers=auth,
                    json={"title": "t", "domain": "finance",
                          "problem_type": "duplicate_charge",
                          "user_claim": "Kartly charged me twice", "evidence_text": ""})
        rows = client.get("/api/cases").json()["cases"]
        assert rows
        assert all(c["case_id"].startswith("PX-") for c in rows)

    def test_empty_claim_is_rejected(self, client, auth):
        r = client.post("/api/cases", headers=auth, json={
            "title": "", "domain": "", "problem_type": "", "user_claim": "",
            "evidence_text": ""})
        assert r.status_code == 400


def test_prototypes_are_no_longer_imported_by_the_app():
    """core/booking_inspector.py, core/classifier.py and core/case_tracker.py
    remain on disk for reference but must not back any live route."""
    import pathlib
    src = pathlib.Path("app/main.py").read_text(encoding="utf-8")
    for mod in ("booking_inspector", "classifier", "case_tracker"):
        assert f"from core import {mod}" not in src, (
            f"{mod} is imported again — the shadow system is back")
