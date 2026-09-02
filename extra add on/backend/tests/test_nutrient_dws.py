"""Nutrient DWS client tests.

These exist because of a bug that only a real API key would have exposed:
POST /sign returns the signed PDF as a *binary stream*, but the client
called resp.json() on it. That raises on PDF bytes, the Evidence Agent's
broad except swallowed it, and DWS sealing would have silently failed for
every record even with a perfectly valid key - the integration would look
wired up and never once work.

The HTTP layer is mocked so the request shape and response handling are
verified without a key or network. The shapes asserted here match
Nutrient's published API reference:
  POST /build  -F instructions='{"parts":[{"html":"<name>"}]}' -F <name>=@file
  POST /sign   -F file=@document.pdf  ->  signed PDF binary
"""

from __future__ import annotations

import hashlib

import pytest

from app.integrations import nutrient_dws
from app.integrations.nutrient_dws import NutrientDWSError

MINIMAL_PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


class _Resp:
    def __init__(self, status=200, content=b"", text=""):
        self.status_code = status
        self.content = content
        self.text = text or content.decode("latin-1", "replace")

    def json(self):
        import json as _json

        return _json.loads(self.content.decode())


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv("NUTRIENT_API_KEY", "tok_test_key")


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.delenv("NUTRIENT_API_KEY", raising=False)


# --- configuration / honesty ---------------------------------------------


def test_not_configured_without_a_key(no_key):
    assert nutrient_dws.is_configured() is False


def test_every_call_raises_clearly_without_a_key(no_key, tmp_path):
    with pytest.raises(NutrientDWSError, match="NUTRIENT_API_KEY"):
        nutrient_dws.html_to_pdf("<p>x</p>", str(tmp_path / "a.pdf"))


def test_preflight_reports_missing_key_rather_than_raising(no_key):
    assert nutrient_dws.preflight() == {"ok": False, "reason": "NUTRIENT_API_KEY is not set"}


# --- POST /build ----------------------------------------------------------


def test_build_sends_the_documented_multipart_shape(with_key, monkeypatch, tmp_path):
    seen = {}

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        seen.update(url=url, headers=headers, files=files, data=data)
        return _Resp(200, MINIMAL_PDF)

    monkeypatch.setattr(nutrient_dws.requests, "post", fake_post)
    out = nutrient_dws.html_to_pdf("<h1>hi</h1>", str(tmp_path / "o.pdf"))

    assert seen["url"] == "https://api.nutrient.io/build"
    assert seen["headers"]["Authorization"] == "Bearer tok_test_key"
    # The instructions JSON must reference the same part name the file is
    # uploaded under, or DWS can't resolve the HTML.
    import json as _json

    instructions = _json.loads(seen["data"]["instructions"])
    referenced = instructions["parts"][0]["html"]
    assert referenced in seen["files"]
    assert open(out, "rb").read() == MINIMAL_PDF


def test_build_raises_on_http_error(with_key, monkeypatch, tmp_path):
    monkeypatch.setattr(
        nutrient_dws.requests, "post", lambda *a, **k: _Resp(401, b"", "unauthorized")
    )
    with pytest.raises(NutrientDWSError, match="401"):
        nutrient_dws.html_to_pdf("<p>x</p>", str(tmp_path / "a.pdf"))


# --- POST /sign -----------------------------------------------------------


def test_sign_writes_the_returned_binary_and_hashes_it(with_key, monkeypatch, tmp_path):
    """The regression this file exists for: the response is a PDF, not JSON."""
    src = tmp_path / "in.pdf"
    src.write_bytes(MINIMAL_PDF)
    signed_bytes = MINIMAL_PDF + b"<signed>"

    seen = {}

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        seen.update(url=url, files=files)
        return _Resp(200, signed_bytes)

    monkeypatch.setattr(nutrient_dws.requests, "post", fake_post)
    out = tmp_path / "out.signed.pdf"
    result = nutrient_dws.sign_evidence_report(str(src), str(out))

    assert seen["url"] == "https://api.nutrient.io/sign"
    assert "file" in seen["files"]  # documented field name
    # `data` is required: omitting it gets a real 400 back with
    # failingPaths [{"path": "$.data", "details": "must be present"}], and it
    # must carry an application/json content type.
    assert "data" in seen["files"]
    _name, body, content_type = seen["files"]["data"]
    assert content_type == "application/json"
    import json as _json

    assert _json.loads(body) == {}  # empty object = invisible signature
    assert out.read_bytes() == signed_bytes
    assert result["sha256"] == hashlib.sha256(signed_bytes).hexdigest()
    assert result["bytes"] == len(signed_bytes)


def test_sign_rejects_a_response_that_is_not_a_pdf(with_key, monkeypatch, tmp_path):
    """A 200 carrying an error body must not be recorded as a seal."""
    src = tmp_path / "in.pdf"
    src.write_bytes(MINIMAL_PDF)
    monkeypatch.setattr(
        nutrient_dws.requests, "post", lambda *a, **k: _Resp(200, b'{"error":"quota exceeded"}')
    )
    with pytest.raises(NutrientDWSError, match="not a PDF"):
        nutrient_dws.sign_evidence_report(str(src), str(tmp_path / "o.pdf"))


def test_sign_raises_on_http_error(with_key, monkeypatch, tmp_path):
    src = tmp_path / "in.pdf"
    src.write_bytes(MINIMAL_PDF)
    monkeypatch.setattr(nutrient_dws.requests, "post", lambda *a, **k: _Resp(403, b"", "forbidden"))
    with pytest.raises(NutrientDWSError, match="403"):
        nutrient_dws.sign_evidence_report(str(src), str(tmp_path / "o.pdf"))


# --- full round trip + Evidence Agent integration -------------------------


def test_seal_round_trip_builds_then_signs(with_key, monkeypatch, tmp_path):
    calls = []

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        calls.append(url)
        return _Resp(200, MINIMAL_PDF if url.endswith("/build") else MINIMAL_PDF + b"<s>")

    monkeypatch.setattr(nutrient_dws.requests, "post", fake_post)
    result = nutrient_dws.seal_evidence_document(
        "<p>report</p>", str(tmp_path / "r.pdf"), str(tmp_path / "r.signed.pdf")
    )

    assert calls == ["https://api.nutrient.io/build", "https://api.nutrient.io/sign"]
    assert result["sha256"]
    assert (tmp_path / "r.signed.pdf").exists()


def test_evidence_agent_records_a_verifiable_seal(with_key, monkeypatch):
    """The stored seal must be recomputable by anyone holding the signed PDF,
    not an opaque id a reader has no way to check."""
    from app.agents import evidence_agent

    signed = MINIMAL_PDF + b"<s>"
    monkeypatch.setattr(
        evidence_agent.nutrient_dws,
        "seal_evidence_document",
        lambda *a, **k: {"sha256": hashlib.sha256(signed).hexdigest(), "bytes": len(signed)},
    )
    seal = evidence_agent._maybe_dws_seal("F-1", {"finding_id": "F-1", "timeline": []}, "sha256:x")
    assert seal == f"dws:sha256:{hashlib.sha256(signed).hexdigest()}"


def test_evidence_agent_returns_no_seal_when_dws_fails(with_key, monkeypatch):
    """A DWS failure must never yield a fabricated seal - the record keeps
    its own SHA-256 signature and honestly reports no DWS seal."""
    from app.agents import evidence_agent

    def boom(*a, **k):
        raise NutrientDWSError("quota exceeded")

    monkeypatch.setattr(evidence_agent.nutrient_dws, "seal_evidence_document", boom)
    assert evidence_agent._maybe_dws_seal("F-1", {"finding_id": "F-1", "timeline": []}, "sha256:x") is None


def test_no_seal_when_unconfigured(no_key):
    from app.agents import evidence_agent

    assert evidence_agent._maybe_dws_seal("F-1", {"finding_id": "F-1", "timeline": []}, "sha256:x") is None


# --- running out of DWS credits ------------------------------------------


def test_sign_reports_credit_exhaustion_clearly(monkeypatch):
    """DWS /sign returns 402 when the account is out of credits. It reads
    nothing like an auth failure, and confusing the two sends someone
    rotating a perfectly good API key during a demo."""
    from app.integrations import nutrient_dws

    class _R:
        status_code = 402
        content = b""
        text = (
            '{"error":{"details":"operation failed because 10 required credits are not '
            'available","status":402}}'
        )

    monkeypatch.setattr(nutrient_dws.requests, "post", lambda *a, **k: _R())
    monkeypatch.setattr(nutrient_dws, "_api_key", lambda: "test-key")

    import tempfile, os

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(b"%PDF-1.7 test")
        src = f.name
    try:
        with pytest.raises(nutrient_dws.NutrientDWSError) as e:
            nutrient_dws.sign_evidence_report(src, src + ".signed")
        assert "402" in str(e.value)
        assert "credits" in str(e.value)
    finally:
        os.unlink(src)


def test_a_seal_failure_never_fabricates_a_seal(monkeypatch, tmp_path):
    """The record must still be produced, signed with SHA-256, and must not
    claim a DWS seal that was never issued. Losing the evidence because the
    optional seal failed would be far worse than having no seal."""
    from app.agents import evidence_agent

    monkeypatch.setattr(evidence_agent, "EVIDENCE_DIR", tmp_path)
    monkeypatch.setattr(evidence_agent.nutrient_dws, "is_configured", lambda: True)

    def _boom(*_a, **_k):
        raise evidence_agent.nutrient_dws.NutrientDWSError(
            "Nutrient DWS /sign error 402: required credits aren't available"
        )

    monkeypatch.setattr(evidence_agent.nutrient_dws, "seal_evidence_document", _boom)

    seal = evidence_agent._maybe_dws_seal("F-402", {"finding_id": "F-402"}, "sha256:abc")
    assert seal is None, "a failed seal must be absent, never fabricated"
