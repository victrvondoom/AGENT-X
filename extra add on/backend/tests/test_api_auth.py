"""Endpoint-level authentication tests.

test_auth.py covers the auth module in isolation. These cover the *wiring*:
that the Depends() is actually attached to the endpoints that change state,
and - most importantly - that the Deployment Gate writes the server-verified
principal rather than anything the client sent.

A unit-tested auth module that was never hooked up to a route would pass
test_auth.py completely while leaving the API wide open.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_with_tokens(monkeypatch, tmp_path):
    monkeypatch.setenv("SENTINEL_API_TOKENS", "alice@corp.example:tok_alice,bob@corp.example:tok_bob")
    from app import decisions, server

    # Keep decisions out of the real workdir.
    # Tokens are read from os.environ per request, so monkeypatching the env
    # is enough - no module reload needed.
    monkeypatch.setattr(decisions, "DECISIONS_PATH", tmp_path / "decisions.json")
    return TestClient(server.app)


@pytest.fixture
def client_open(monkeypatch, tmp_path):
    monkeypatch.delenv("SENTINEL_API_TOKENS", raising=False)
    from app import decisions, server

    monkeypatch.setattr(decisions, "DECISIONS_PATH", tmp_path / "decisions.json")
    return TestClient(server.app)


DECISION = {"finding_id": "SENTINEL-F-TEST", "decision": "approved"}


def test_decision_rejected_without_a_token(client_with_tokens):
    r = client_with_tokens.post("/api/decisions", json=DECISION)
    assert r.status_code == 401


def test_decision_rejected_with_a_bad_token(client_with_tokens):
    r = client_with_tokens.post(
        "/api/decisions", json=DECISION, headers={"Authorization": "Bearer wrong"}
    )
    assert r.status_code == 401


def test_decision_records_the_authenticated_principal(client_with_tokens):
    """The core guarantee: who approved is decided by the server."""
    r = client_with_tokens.post(
        "/api/decisions", json=DECISION, headers={"Authorization": "Bearer tok_alice"}
    )
    assert r.status_code == 200
    assert r.json()["actor"] == "alice@corp.example"


def test_client_cannot_forge_the_actor(client_with_tokens):
    """Even if a client sends an actor field, it must be ignored - this is
    the exact forgery the endpoint previously allowed."""
    r = client_with_tokens.post(
        "/api/decisions",
        json={**DECISION, "actor": "someone-else@evil.example"},
        headers={"Authorization": "Bearer tok_bob"},
    )
    assert r.status_code == 200
    assert r.json()["actor"] == "bob@corp.example"


def test_open_mode_labels_the_principal_as_unauthenticated(client_open):
    """With no tokens configured the demo still works, but the record must
    say the identity was never verified."""
    r = client_open.post("/api/decisions", json=DECISION)
    assert r.status_code == 200
    assert "unauthenticated" in r.json()["actor"]


def test_invalid_decision_value_is_rejected(client_with_tokens):
    r = client_with_tokens.post(
        "/api/decisions",
        json={"finding_id": "F", "decision": "maybe"},
        headers={"Authorization": "Bearer tok_alice"},
    )
    assert r.status_code == 400


@pytest.mark.parametrize(
    "method,path",
    [("post", "/api/findings/refresh"), ("post", "/api/jobs/some-id/abort")],
)
def test_other_mutating_endpoints_require_auth(client_with_tokens, method, path):
    r = getattr(client_with_tokens, method)(path, json={})
    assert r.status_code == 401


def test_read_endpoints_stay_public(client_with_tokens):
    """Reads are intentionally unauthenticated - the dashboard polls them
    constantly and they expose no ability to change state."""
    assert client_with_tokens.get("/api/system-info").status_code == 200


# The two client fixtures are mutually exclusive - they set and unset the
# same env var - so the posture is asserted in one test each rather than
# both in one, which would just clobber the first fixture's setup.
def test_system_info_reports_auth_enabled(client_with_tokens):
    assert client_with_tokens.get("/api/system-info").json()["auth_enabled"] is True


def test_system_info_reports_auth_disabled_in_open_mode(client_open):
    assert client_open.get("/api/system-info").json()["auth_enabled"] is False
