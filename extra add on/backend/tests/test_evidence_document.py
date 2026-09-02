"""Evidence document + dual-seal verification tests.

A CAdES-signed PDF that nobody can open proves nothing: the whole claim is
that a third party can verify the seal in ordinary PDF tooling, which
requires the artifact to actually be reachable. These cover serving it, and
verifying the two seals independently.

The two seals cover different things and can legitimately disagree - the
content signature covers the record's JSON, the DWS seal covers the signed
PDF - so a record whose JSON is intact but whose PDF was swapped must fail,
and must say which half failed.
"""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

MINIMAL_PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\ntrailer\n<<>>\n%%EOF\n"
FID = "SENTINEL-F-TEST-DOC"


@pytest.fixture
def client(monkeypatch, tmp_path):
    from app import server
    from app.agents import evidence_agent

    monkeypatch.setattr(evidence_agent, "EVIDENCE_DIR", tmp_path)
    return TestClient(server.app)


def _seal(record: dict) -> dict:
    from app.agents.evidence_agent import _sign

    payload = {k: record.get(k) for k in
               ("finding_id", "repo", "commit", "timeline", "final_status",
                "verdict", "verification_results", "patch_proposal")}
    return {**record, "signature": _sign(payload)}


def _store(monkeypatch, record):
    from app import server

    class _S:
        def get_evidence(self, fid):
            return record if fid == record["finding_id"] else None

    monkeypatch.setattr(server, "get_store", lambda: _S())


BASE = {"finding_id": FID, "repo": "r", "commit": None, "timeline": [],
        "final_status": "RESOLVED", "verdict": None,
        "verification_results": [], "patch_proposal": None}


# --- serving the document -------------------------------------------------


def test_signed_pdf_is_served_inline_for_embedding(client, monkeypatch, tmp_path):
    (tmp_path / f"{FID}.signed.pdf").write_bytes(MINIMAL_PDF)
    r = client.get(f"/api/evidence/{FID}/document?variant=signed")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith("inline")
    assert r.content == MINIMAL_PDF


def test_download_forces_attachment(client, monkeypatch, tmp_path):
    """The HTML download attribute is ignored cross-origin, so the save has
    to be driven by Content-Disposition from the server."""
    (tmp_path / f"{FID}.signed.pdf").write_bytes(MINIMAL_PDF)
    r = client.get(f"/api/evidence/{FID}/document?variant=signed&download=true")
    assert r.headers["content-disposition"].startswith("attachment")


def test_unsigned_variant_is_served_separately(client, tmp_path):
    (tmp_path / f"{FID}.pdf").write_bytes(MINIMAL_PDF)
    assert client.get(f"/api/evidence/{FID}/document?variant=unsigned").status_code == 200
    # the signed one doesn't exist, and must not silently fall back to it
    assert client.get(f"/api/evidence/{FID}/document?variant=signed").status_code == 404


def test_missing_pdf_explains_why(client):
    r = client.get(f"/api/evidence/{FID}/document?variant=signed")
    assert r.status_code == 404
    assert "NUTRIENT_API_KEY" in r.json()["detail"]


def test_invalid_variant_rejected(client):
    assert client.get(f"/api/evidence/{FID}/document?variant=../../etc/passwd").status_code == 400


# --- dual-seal verification ----------------------------------------------


def test_both_seals_valid(client, monkeypatch, tmp_path):
    (tmp_path / f"{FID}.signed.pdf").write_bytes(MINIMAL_PDF)
    rec = _seal({**BASE, "dws_seal": f"dws:sha256:{hashlib.sha256(MINIMAL_PDF).hexdigest()}"})
    _store(monkeypatch, rec)

    body = client.get(f"/api/evidence/{FID}/verify").json()
    assert body["valid"] is True
    assert body["content_signature"]["valid"] is True
    assert body["dws"]["valid"] is True
    assert body["dws"]["bytes"] == len(MINIMAL_PDF)


def test_tampered_pdf_fails_dws_while_content_signature_still_passes(client, monkeypatch, tmp_path):
    """The case a single seal would miss entirely: the record's JSON is
    untouched, but the signed artifact was swapped."""
    (tmp_path / f"{FID}.signed.pdf").write_bytes(MINIMAL_PDF + b"tampered")
    rec = _seal({**BASE, "dws_seal": f"dws:sha256:{hashlib.sha256(MINIMAL_PDF).hexdigest()}"})
    _store(monkeypatch, rec)

    body = client.get(f"/api/evidence/{FID}/verify").json()
    assert body["content_signature"]["valid"] is True   # JSON is fine
    assert body["dws"]["valid"] is False                # artifact is not
    assert body["valid"] is False                       # so overall fails
    assert "does not match" in body["dws"]["reason"]


def test_deleted_signed_pdf_is_reported(client, monkeypatch):
    rec = _seal({**BASE, "dws_seal": "dws:sha256:" + "0" * 64})
    _store(monkeypatch, rec)
    body = client.get(f"/api/evidence/{FID}/verify").json()
    assert body["dws"]["valid"] is False
    assert "missing" in body["dws"]["reason"]


def test_record_without_a_dws_seal_is_not_penalised(client, monkeypatch):
    """No key configured is a valid state - the record still has its own
    signature and must not be reported as failing."""
    rec = _seal({**BASE, "dws_seal": None})
    _store(monkeypatch, rec)
    body = client.get(f"/api/evidence/{FID}/verify").json()
    assert body["dws"]["present"] is False
    assert body["dws"]["valid"] is None
    assert body["valid"] is True


def test_edited_record_fails_content_signature(client, monkeypatch):
    rec = _seal({**BASE, "dws_seal": None})
    rec["final_status"] = "CONFIRMED_EXPLOITABLE"   # tamper after sealing
    _store(monkeypatch, rec)
    body = client.get(f"/api/evidence/{FID}/verify").json()
    assert body["content_signature"]["valid"] is False
    assert body["valid"] is False


# --- immutable signed-artifact archiving ---------------------------------


def test_a_record_resolves_to_the_artifact_it_was_sealed_with(tmp_path, monkeypatch):
    """Re-investigating a finding used to overwrite the previous run's
    signed PDF, orphaning every earlier record: the seal still named a
    digest, but the only file on disk was a different run's document, and
    verification reported a mismatch indistinguishable from tampering."""
    import hashlib

    from app.agents import evidence_agent

    monkeypatch.setattr(evidence_agent, "EVIDENCE_DIR", tmp_path)

    old_bytes = b"%PDF-1.7 first run"
    old_digest = hashlib.sha256(old_bytes).hexdigest()
    (tmp_path / f"F-1.{old_digest[:16]}.signed.pdf").write_bytes(old_bytes)

    # A later run clobbers the finding-keyed path with different content.
    (tmp_path / "F-1.signed.pdf").write_bytes(b"%PDF-1.7 second run, different bytes")

    resolved = evidence_agent.signed_pdf_path("F-1", f"dws:sha256:{old_digest}")
    assert resolved is not None
    assert resolved.read_bytes() == old_bytes, "must resolve to its own sealed artifact"


def test_legacy_records_still_resolve(tmp_path, monkeypatch):
    """Records sealed before archiving existed have no digest-named copy and
    must keep working rather than 404ing."""
    from app.agents import evidence_agent

    monkeypatch.setattr(evidence_agent, "EVIDENCE_DIR", tmp_path)
    (tmp_path / "F-2.signed.pdf").write_bytes(b"%PDF legacy")

    resolved = evidence_agent.signed_pdf_path("F-2", "dws:sha256:" + "0" * 64)
    assert resolved is not None and resolved.name == "F-2.signed.pdf"


def test_missing_artifact_resolves_to_none(tmp_path, monkeypatch):
    from app.agents import evidence_agent

    monkeypatch.setattr(evidence_agent, "EVIDENCE_DIR", tmp_path)
    assert evidence_agent.signed_pdf_path("F-3", "dws:sha256:abc") is None


def test_archiving_is_idempotent_and_never_raises(tmp_path, monkeypatch):
    """Archiving is a durability nicety; it must never be able to lose the
    seal it is trying to protect."""
    import hashlib

    from app.agents import evidence_agent

    monkeypatch.setattr(evidence_agent, "EVIDENCE_DIR", tmp_path)
    src = tmp_path / "F-4.signed.pdf"
    payload = b"%PDF archived"
    src.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()

    evidence_agent._archive_signed_pdf("F-4", src, digest)
    evidence_agent._archive_signed_pdf("F-4", src, digest)  # twice is fine
    assert (tmp_path / f"F-4.{digest[:16]}.signed.pdf").read_bytes() == payload

    # A vanished source must not blow up the sealing path.
    evidence_agent._archive_signed_pdf("F-5", tmp_path / "does-not-exist.pdf", "deadbeef" * 8)
