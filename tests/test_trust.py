"""
pytest suite for the shared trust spine.

Split deliberately: the tests that need no database run anywhere, and the ones that
do skip cleanly rather than failing. A suite that goes red because a developer has
no Postgres running teaches people to ignore red.

    pytest tests/ -v
    DATABASE_URL=postgresql://... pytest tests/ -v      # includes the DB tests
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.trust import audit, certificate, gate          # noqa: E402
from pipelines.document import generate                   # noqa: E402

DSN = os.environ.get("DATABASE_URL")
# .env.example ships DATABASE_URL as an unfilled template
# (postgresql://USER:PASSWORD@HOST:...) so `python-dotenv` loads something
# non-empty even when nobody has pointed it at a real cluster yet. Treating that
# placeholder as "set" broke the one guarantee this module promises: it doesn't
# fail red for a developer with no database, it skips. `not DSN` let a template
# value through and turned a clean skip into a real connection attempt against
# the literal host "HOST".
_UNFILLED = DSN and ("USER:PASSWORD@HOST" in DSN or "@HOST:" in DSN)
needs_db = pytest.mark.skipif(not DSN or _UNFILLED,
                              reason="DATABASE_URL not set (or still the template)")


# ═══════════════════════════════════════════════════════════════════════════
# no database required
# ═══════════════════════════════════════════════════════════════════════════
class TestCanonical:
    def test_key_order_does_not_change_the_hash(self):
        a = audit.compute_hash(audit.GENESIS, {"b": 2, "a": 1})
        b = audit.compute_hash(audit.GENESIS, {"a": 1, "b": 2})
        assert a == b, "canonicalisation must be order-independent or hashes drift"

    def test_any_content_change_changes_the_hash(self):
        base = audit.compute_hash(audit.GENESIS, {"a": 1})
        assert base != audit.compute_hash(audit.GENESIS, {"a": 2})
        assert base != audit.compute_hash("1" * 64, {"a": 1})

    def test_no_incidental_whitespace(self):
        """A verifier in another language must be able to reproduce these bytes."""
        assert audit.canonical({"b": 1, "a": 2}) == '{"a":2,"b":1}'

    def test_python_and_js_canonical_agree_on_shape(self):
        # the JS verifier sorts keys and uses JSON.stringify for scalars; nested
        # structures must serialise identically or offline verification fails
        payload = {"z": [1, {"b": None, "a": True}], "a": "x"}
        assert certificate.canonical(payload) == b'{"a":"x","z":[1,{"a":true,"b":null}]}'


class TestGate:
    def test_absent_confidence_goes_to_a_human(self):
        d = gate.route("total", None)
        assert d.decision == gate.HUMAN
        assert d.reason == "no_confidence_signal"

    def test_absent_is_not_treated_as_zero(self):
        """Nutrient: absent 'doesn't mean low confidence'. Distinct reason codes."""
        assert gate.route("total", None).reason != gate.route("total", 0.0).reason

    def test_money_is_held_to_a_higher_bar_than_prose(self):
        assert gate.threshold_for("total") > gate.threshold_for("description")

    def test_high_confidence_over_illegible_source_is_held(self):
        """The case a flat threshold gets wrong: sure about text it cannot see."""
        d = gate.route("account_number", 0.99, recognition=0.41)
        assert d.decision == gate.HUMAN
        assert d.reason == "illegible_source"

    def test_nested_field_paths_inherit_policy(self):
        assert gate.threshold_for("line_items[0].amount") == gate.threshold_for("amount")
        assert gate.threshold_for("line_items[0].amount") != gate.DEFAULT_THRESHOLD

    def test_out_of_range_confidence_is_refused(self):
        assert gate.route("total", 1.7).reason == "confidence_out_of_range"

    def test_clean_field_is_auto(self):
        assert gate.route("invoice_number", 0.99, 0.95).decision == gate.AUTO

    def test_policy_snapshot_is_serialisable(self):
        json.dumps(gate.policy_snapshot())     # must survive the audit trail


class TestPdfRoundTrip:
    def test_values_survive_generate_then_read(self):
        f = {"total": "12,480.00", "vendor": "Acme (UK) Ltd"}
        assert generate.read_pdf_fields(generate.build_pdf("T", f)) == f

    def test_pdf_special_characters_are_escaped(self):
        f = {"note": "a (b) c \\ d"}
        assert generate.read_pdf_fields(generate.build_pdf("T", f)) == f

    def test_a_byte_flip_changes_the_readback(self):
        """If this fails, self-verify is reading memory, not the artefact."""
        pdf = generate.build_pdf("T", {"total": "12,480.00"})
        i = pdf.find(b"12,480.00")
        assert i > 0
        flipped = pdf[:i] + b"99,999.99" + pdf[i + 9:]
        assert generate.read_pdf_fields(flipped)["total"] == "99,999.99"


class TestCertificateOffline:
    @staticmethod
    def _signed():
        from cryptography.hazmat.primitives.asymmetric import ec
        key = ec.generate_private_key(ec.SECP256R1())
        body = {"spec": certificate.SPEC_VERSION, "job_id": str(uuid.uuid4()),
                "fields": [{"name": "total", "value": "12,480.00", "decision": "HUMAN"}],
                "chain_head": "a" * 64, "chain_length": 3}
        return certificate.sign(body, key), key

    def test_genuine_certificate_verifies_without_a_database(self):
        env, _ = self._signed()
        v = certificate.verify(env, conn=None)
        assert v["checks"]["content_hash"]["ok"] is True
        assert v["checks"]["signature"]["ok"] is True
        assert v["ok"] is True

    def test_edited_body_fails_the_hash(self):
        env, _ = self._signed()
        env["certificate"]["fields"][0]["value"] = "999,999.00"
        assert certificate.verify(env, conn=None)["checks"]["content_hash"]["ok"] is False

    def test_edited_and_rehashed_still_fails_the_signature(self):
        env, _ = self._signed()
        env["certificate"]["fields"][0]["value"] = "999,999.00"
        env["sha256"] = hashlib.sha256(
            certificate.canonical(env["certificate"])).hexdigest()
        v = certificate.verify(env, conn=None)
        assert v["checks"]["content_hash"]["ok"] is True
        assert v["checks"]["signature"]["ok"] is False

    def test_a_certificate_cannot_vouch_for_its_own_key(self):
        """The documented limitation, asserted so it cannot regress silently."""
        from cryptography.hazmat.primitives.asymmetric import ec
        env, _ = self._signed()
        forged = certificate.sign(env["certificate"], ec.generate_private_key(ec.SECP256R1()))
        assert certificate.verify(forged, conn=None)["ok"] is True   # self-consistent
        pinned = certificate.verify(forged, conn=None,
                                    trusted_public_key=env["public_key"])
        assert pinned["checks"]["trusted_key"]["ok"] is False
        assert pinned["ok"] is False

    def test_der_to_raw_is_always_64_bytes(self):
        """Short r or s must be left-padded, or WebCrypto silently rejects."""
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        key = ec.generate_private_key(ec.SECP256R1())
        for i in range(40):                       # enough draws to hit a short integer
            der = key.sign(f"m{i}".encode(), ec.ECDSA(hashes.SHA256()))
            assert len(certificate._der_to_raw(der)) == 64


# ═══════════════════════════════════════════════════════════════════════════
# database required
# ═══════════════════════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def conn():
    import psycopg
    c = psycopg.connect(DSN, autocommit=True, connect_timeout=10)
    yield c
    c.close()


@pytest.fixture
def job(conn):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO jobs (kind, doc_type, status) "
                    "VALUES ('document','invoice','EXTRACTING') RETURNING id::text")
        jid = cur.fetchone()[0]
    yield jid
    with conn.cursor() as cur:
        cur.execute("DELETE FROM jobs WHERE id = %s", (jid,))


@needs_db
class TestChain:
    def test_appends_link_and_verify(self, conn, job):
        a = audit.append_audit(conn, job, "one", "AGENT", {"i": 1})
        b = audit.append_audit(conn, job, "two", "AGENT", {"i": 2})
        assert a["prev_hash"] == audit.GENESIS
        assert b["prev_hash"] == a["content_hash"]
        assert audit.verify_chain(conn, job)["ok"]

    def test_edited_row_is_caught(self, conn, job):
        audit.append_audit(conn, job, "one", "AGENT", {"i": 1})
        audit.append_audit(conn, job, "two", "AGENT", {"i": 2})
        with conn.cursor() as cur:
            cur.execute("UPDATE audit_log SET detail=%s, detail_canonical=%s "
                        "WHERE job_id=%s AND seq=0",
                        (json.dumps({"i": 99}), '{"i":99}', job))
        assert audit.verify_chain(conn, job)["ok"] is False

    def test_readable_and_hashed_columns_must_agree(self, conn, job):
        """Editing only the readable column would display a forgery as clean."""
        audit.append_audit(conn, job, "one", "AGENT", {"i": 1})
        with conn.cursor() as cur:
            cur.execute("UPDATE audit_log SET detail=%s WHERE job_id=%s AND seq=0",
                        (json.dumps({"i": 99}), job))
        r = audit.verify_chain(conn, job)
        assert r["ok"] is False and "divergence" in r["reason"]

    def test_deleted_middle_row_is_caught(self, conn, job):
        for i in range(3):
            audit.append_audit(conn, job, f"s{i}", "AGENT", {"i": i})
        with conn.cursor() as cur:
            cur.execute("DELETE FROM audit_log WHERE job_id=%s AND seq=1", (job,))
        r = audit.verify_chain(conn, job)
        assert r["ok"] is False and "sequence gap" in r["reason"]

    def test_unknown_actor_is_refused(self, conn, job):
        with pytest.raises(ValueError):
            audit.append_audit(conn, job, "x", "ROBOT", {})

    def test_unknown_job_is_refused(self, conn):
        with pytest.raises(ValueError):
            audit.append_audit(conn, str(uuid.uuid4()), "x", "AGENT", {})


@needs_db
class TestReviewGate:
    def test_job_cannot_resume_with_work_outstanding(self, conn, job):
        from pipelines.document import pipeline, review
        pipeline.ingest_prepared(conn, job, [
            {"name": "total", "value": "1", "confidence": 0.10, "recognition": 0.9},
        ], engine="pytest")
        assert review.progress(conn, job)["status"] == "NEEDS_REVIEW"
        assert review._resume(conn, job, "cheat") is False
        assert review.progress(conn, job)["status"] == "NEEDS_REVIEW"

    def test_last_ruling_resumes(self, conn, job):
        from pipelines.document import pipeline, review
        pipeline.ingest_prepared(conn, job, [
            {"name": "total", "value": "1", "confidence": 0.10, "recognition": 0.9},
        ], engine="pytest")
        f = review.pending(conn, job)[0]
        out = review.decide(conn, job, f["id"], "CORRECT", "tester", "2")
        assert out["resumed"] is True
        assert review.progress(conn, job)["status"] == "APPROVED"

    def test_a_field_cannot_be_ruled_twice(self, conn, job):
        from pipelines.document import pipeline, review
        pipeline.ingest_prepared(conn, job, [
            {"name": "total", "value": "1", "confidence": 0.10, "recognition": 0.9},
            {"name": "tax", "value": "1", "confidence": 0.10, "recognition": 0.9},
        ], engine="pytest")
        f = review.pending(conn, job)[0]
        review.decide(conn, job, f["id"], "ACCEPT", "a")
        with pytest.raises(review.ReviewError):
            review.decide(conn, job, f["id"], "ACCEPT", "b")
