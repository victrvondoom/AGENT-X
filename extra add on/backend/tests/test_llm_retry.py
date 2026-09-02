"""Transient-failure handling around the Gemini call.

A real ADK investigation died after 324 seconds on a Gemini 503 - the model
was briefly overloaded. By then the run had already done a clone, an
npm audit and a sandboxed exploit attempt, all of which was thrown away
because only 429 was treated as retryable. During a judged demo that is the
difference between a working product and a stack trace.
"""

from __future__ import annotations

import pytest
import requests

from app import llm


class _Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


_OK = {"candidates": [{"content": {"parts": [{"text": "done"}]}}]}


@pytest.fixture(autouse=True)
def _no_sleeping(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda _s: None)
    monkeypatch.setattr(llm, "GEMINI_API_KEY", "test-key")


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_failures_are_retried_and_can_succeed(monkeypatch, status):
    calls = {"n": 0}

    def _post(*_a, **_k):
        calls["n"] += 1
        return _Resp(status) if calls["n"] == 1 else _Resp(200, _OK)

    monkeypatch.setattr(llm.requests, "post", _post)
    assert llm.call_gemini("hi") == "done"
    assert calls["n"] == 2, f"{status} should have been retried"


def test_a_client_error_is_not_retried(monkeypatch):
    """400 means the request itself is wrong. Retrying it just burns time
    and quota to get the same answer."""
    calls = {"n": 0}

    def _post(*_a, **_k):
        calls["n"] += 1
        return _Resp(400, text="bad request")

    monkeypatch.setattr(llm.requests, "post", _post)
    with pytest.raises(llm.LLMError):
        llm.call_gemini("hi")
    assert calls["n"] == 1


def test_persistent_outage_eventually_raises(monkeypatch):
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: _Resp(503, text="overloaded"))
    with pytest.raises(llm.LLMError, match="503"):
        llm.call_gemini("hi")


def test_connection_errors_are_retried_too(monkeypatch):
    """A dropped connection is transient in the same way a 503 is."""
    calls = {"n": 0}

    def _post(*_a, **_k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.ConnectionError("connection reset")
        return _Resp(200, _OK)

    monkeypatch.setattr(llm.requests, "post", _post)
    assert llm.call_gemini("hi") == "done"
    assert calls["n"] == 3


def test_a_permanent_network_failure_surfaces_as_llm_error(monkeypatch):
    monkeypatch.setattr(
        llm.requests, "post", lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError("down"))
    )
    with pytest.raises(llm.LLMError, match="unreachable"):
        llm.call_gemini("hi")


def test_backoff_is_jittered(monkeypatch):
    """Six agents hitting the same overloaded model must not retry in
    lockstep, or they collide again on every attempt."""
    samples = {llm._backoff(2) for _ in range(20)}
    assert len(samples) > 1, "backoff must not be identical across callers"


# --- distinguishing quota exhaustion from transient overload --------------


def test_quota_exhaustion_is_named_as_an_account_limit(monkeypatch):
    """Quota and overload both arrive as 4xx/5xx and read almost the same,
    but the responses are opposite. During a timed demo, a bare "429" sends
    people hunting for a bug that is not in this codebase."""
    monkeypatch.setattr(
        llm.requests,
        "post",
        lambda *a, **k: _Resp(429, text='{"error":{"message":"You exceeded your current quota"}}'),
    )
    with pytest.raises(llm.LLMError) as e:
        llm.call_gemini("hi")
    msg = str(e.value)
    assert "quota exhausted" in msg
    assert "retrying will not clear it" in msg
    assert llm.MODEL in msg, "must name which model ran out"


def test_persistent_overload_is_named_as_upstream_capacity(monkeypatch):
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: _Resp(503, text="overloaded"))
    with pytest.raises(llm.LLMError, match="temporarily unavailable"):
        llm.call_gemini("hi")


def test_an_ordinary_error_keeps_its_raw_shape(monkeypatch):
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: _Resp(400, text="bad schema"))
    with pytest.raises(llm.LLMError, match="Gemini API error 400"):
        llm.call_gemini("hi")
