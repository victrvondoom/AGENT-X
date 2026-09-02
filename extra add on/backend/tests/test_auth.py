"""Authentication tests.

These cover the specific failure this module exists to prevent: the
Deployment Gate records who approved shipping a security patch, and before
authentication existed that name was whatever string the client sent - so
the system's central claim ("a human reviewed this") was forgeable and the
audit trail recorded the forgery as fact.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import auth


@pytest.fixture(autouse=True)
def _clear_tokens(monkeypatch):
    monkeypatch.delenv("SENTINEL_API_TOKENS", raising=False)


def test_local_dev_mode_is_labelled_not_silently_trusted(monkeypatch):
    """With no tokens configured the API stays usable, but anything it
    records must say so rather than implying a verified human."""
    assert auth.auth_enabled() is False
    principal = auth.resolve_principal(None)
    assert principal == auth.UNAUTHENTICATED_PRINCIPAL
    assert "unauthenticated" in principal


def test_enabled_when_tokens_configured(monkeypatch):
    monkeypatch.setenv("SENTINEL_API_TOKENS", "alice@corp.example:tok_a1b2")
    assert auth.auth_enabled() is True


def test_valid_token_resolves_to_its_principal(monkeypatch):
    monkeypatch.setenv("SENTINEL_API_TOKENS", "alice@corp.example:tok_a1b2,bob@corp.example:tok_c3d4")
    assert auth.resolve_principal("Bearer tok_a1b2") == "alice@corp.example"
    assert auth.resolve_principal("Bearer tok_c3d4") == "bob@corp.example"


def test_bearer_scheme_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("SENTINEL_API_TOKENS", "alice@corp.example:tok_a1b2")
    assert auth.resolve_principal("bearer tok_a1b2") == "alice@corp.example"


@pytest.mark.parametrize(
    "header",
    [None, "", "tok_a1b2", "Basic tok_a1b2", "Bearer", "Bearer wrong_token"],
    ids=["missing", "empty", "no-scheme", "wrong-scheme", "no-token", "bad-token"],
)
def test_rejects_anything_that_is_not_a_valid_bearer_token(monkeypatch, header):
    monkeypatch.setenv("SENTINEL_API_TOKENS", "alice@corp.example:tok_a1b2")
    with pytest.raises(HTTPException) as exc:
        auth.resolve_principal(header)
    assert exc.value.status_code == 401


def test_malformed_token_config_entries_do_not_grant_access(monkeypatch):
    """A malformed pair must be skipped, never treated as a wildcard."""
    monkeypatch.setenv("SENTINEL_API_TOKENS", "no_colon_here,:empty_principal,alice@corp.example:tok_ok")
    assert auth.resolve_principal("Bearer tok_ok") == "alice@corp.example"
    for bad in ["Bearer no_colon_here", "Bearer empty_principal", "Bearer "]:
        with pytest.raises(HTTPException):
            auth.resolve_principal(bad)


def test_token_is_not_confusable_with_principal(monkeypatch):
    """Presenting the principal name instead of the token must not work."""
    monkeypatch.setenv("SENTINEL_API_TOKENS", "alice@corp.example:tok_a1b2")
    with pytest.raises(HTTPException):
        auth.resolve_principal("Bearer alice@corp.example")
