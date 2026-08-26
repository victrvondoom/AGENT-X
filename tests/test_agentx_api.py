"""
The HTTP surface — mounted into the same FastAPI app as the erasure and document
pipelines, auth-gated the same way, and exercised end to end through TestClient.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    os.environ.setdefault("DATABASE_URL", "postgresql://localhost:5432/agentx")
    os.environ["AGENT_X_ENGINE"] = "sqlite"
    db = tmp_path_factory.mktemp("agentx_api") / "agentx.db"
    os.environ["AGENT_X_DB_PATH"] = str(db)

    from agentx import store
    store.use_sqlite(str(db))

    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


@pytest.fixture
def auth_headers():
    token = os.environ.get("AGENT_X_AUTH_TOKEN")
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def test_health_reports_engine_and_ontology(client):
    r = client.get("/api/agentx/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["ontology"]["problem_types"] >= 25


def test_unauthenticated_write_is_rejected_when_a_token_is_configured(client):
    if not os.environ.get("AGENT_X_AUTH_TOKEN"):
        pytest.skip("no AGENT_X_AUTH_TOKEN configured in this environment")
    r = client.post("/api/agentx/cases", json={"description": "test", "use_llm": False})
    assert r.status_code == 401


def test_create_and_fetch_a_case(client, auth_headers):
    r = client.post("/api/agentx/cases", json={
        "description": "Kartly charged me twice for order 402-9938271, "
                       "2,399 INR each on 2026-08-02.",
        "use_llm": False}, headers=auth_headers)
    assert r.status_code == 200
    snap = r.json()
    cid = snap["case"]["id"]
    assert snap["case"]["problem_type"] == "duplicate_charge"

    r2 = client.get(f"/api/agentx/cases/{cid}")
    assert r2.status_code == 200
    assert r2.json()["case"]["id"] == cid


def test_case_list_endpoint_renders_eligibility_headline(client, auth_headers):
    # Regression: list_cases() called eligibility.load()/headline() with no
    # `eligibility` import in scope, so this endpoint 500'd on every request —
    # nothing in the suite exercised GET /api/agentx/cases to catch it.
    r = client.post("/api/agentx/cases", json={
        "description": "Kartly charged me twice for order 402-9938271, "
                       "2,399 INR each on 2026-08-02.",
        "use_llm": False}, headers=auth_headers)
    assert r.status_code == 200

    r2 = client.get("/api/agentx/cases")
    assert r2.status_code == 200
    cases = r2.json()
    assert len(cases) >= 1
    assert "headline" in cases[0]
    assert "amount" in cases[0]


def test_unknown_case_is_404(client):
    r = client.get("/api/agentx/cases/PX-NOSUCHCASE")
    assert r.status_code == 404


def test_understand_endpoint_is_public_and_side_effect_free(client):
    r = client.post("/api/agentx/understand", json={"text": "They charged me again"})
    assert r.status_code == 200
    body = r.json()
    assert body["ambiguous"] is True
    assert len(body["hypotheses"]) >= 4


def test_ontology_and_capabilities_are_public_reads(client):
    assert client.get("/api/agentx/ontology").status_code == 200
    assert client.get("/api/agentx/capabilities").status_code == 200
    assert client.get("/api/agentx/providers").status_code == 200
    assert client.get("/api/agentx/governor").status_code == 200
    r = client.get("/api/agentx/public-key")
    assert r.status_code == 200
    assert "BEGIN PUBLIC KEY" in r.text


def test_demo_scenario_runs_through_http_and_resolves(client, auth_headers):
    r = client.post("/api/agentx/demo/reset", headers=auth_headers)
    assert r.status_code == 200
    r = client.post("/api/agentx/demo/run/E", json={}, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["final_state"] == "RESOLVED"
    assert body["chain"]["ok"]


def test_receipt_verify_endpoint_round_trips(client, auth_headers):
    r = client.post("/api/agentx/demo/run/E", json={}, headers=auth_headers)
    cid = r.json()["case_id"]
    r2 = client.get(f"/api/agentx/cases/{cid}/receipt")
    assert r2.status_code == 200
    env = r2.json()["envelope"]
    r3 = client.post("/api/agentx/receipt/verify", json=env, params={"check_chain": "false"})
    assert r3.status_code == 200
    assert r3.json()["ok"] is True


def test_forget_endpoint_shreds_and_chain_still_verifies(client, auth_headers):
    r = client.post("/api/agentx/cases", json={
        "description": "test case to erase", "use_llm": False}, headers=auth_headers)
    cid = r.json()["case"]["id"]
    r2 = client.post(f"/api/agentx/cases/{cid}/forget", headers=auth_headers)
    assert r2.status_code == 200
    body = r2.json()
    assert body["unrecoverable"] is True
    assert body["chain"]["ok"] is True


def test_demo_world_is_inspectable(client):
    r = client.get("/api/agentx/demo/world")
    assert r.status_code == 200
    assert "companies" in r.json()
