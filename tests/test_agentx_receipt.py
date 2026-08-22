"""
The Resolution Receipt — signed, chain-bound, and honest about verification state.

And the letter grounding check — the mechanism that stops a rewritten letter from
introducing a figure nobody extracted.
"""
from __future__ import annotations

import pytest

from agentx import chain, engine, ids, receipt, store
from agentx import case as case_mod
from agentx.evidence import graph as egraph
from agentx.execution import providers


@pytest.fixture(autouse=True)
def sqlite_engine(tmp_path):
    store.reset_for_tests(str(tmp_path / "agentx_test.db"))
    providers.clear()
    providers.bootstrap()
    yield


@pytest.fixture
def conn():
    with store.connect() as c:
        yield c


@pytest.fixture
def case(conn):
    c = case_mod.create(conn, description="test receipt case", autonomy_level=4)
    return case_mod.update(conn, c["id"], confidence=0.9, problem_type="duplicate_charge",
                           domain="finance")


class TestReceipt:
    def test_receipt_is_signed(self, conn, case):
        env = receipt.issue(conn, case["id"])
        assert env["signed"] is True
        assert "signature" in env

    def test_receipt_hash_is_reproducible(self, conn, case):
        env = receipt.issue(conn, case["id"], store_it=False)
        from core.trust import certificate as cert_mod
        recomputed = cert_mod.canonical(env["receipt"])
        import hashlib
        assert hashlib.sha256(recomputed).hexdigest() == env["sha256"]

    def test_verify_passes_on_a_fresh_receipt(self, conn, case):
        env = receipt.issue(conn, case["id"])
        v = receipt.verify(env, conn=conn)
        assert v["ok"], v

    def test_tampered_receipt_fails_hash_check(self, conn, case):
        env = receipt.issue(conn, case["id"])
        env["receipt"]["readable"]["result"] = "TAMPERED: fully refunded, trust me"
        v = receipt.verify(env, conn=None)
        assert not v["ok"]
        assert v["checks"]["content_hash"]["ok"] is False

    def test_verify_offline_needs_no_database(self, conn, case):
        env = receipt.issue(conn, case["id"])
        v = receipt.verify(env, conn=None)
        assert v["checks"]["content_hash"]["ok"] is True
        assert v["checks"]["signature"]["ok"] is True
        assert v["checks"]["case_chain"]["ok"] is None  # not checked offline

    def test_verify_catches_chain_mismatch(self, conn, case):
        env = receipt.issue(conn, case["id"])
        with conn.cursor() as cur:
            cur.execute("INSERT INTO case_chain (id, case_id, seq, step, actor, detail,"
                        " prev_hash, content_hash, sealed, ts) VALUES"
                        " ('extra-row', %s, 9999, 'fake', 'SYSTEM', '{}', 'x', 'y', 0, %s)",
                        (case["id"], ids.now()))
        v = receipt.verify(env, conn=conn)
        assert not v["checks"]["case_chain"]["ok"]

    def test_no_external_action_reports_not_applicable(self, conn, case):
        env = receipt.issue(conn, case["id"])
        assert env["receipt"]["readable"]["verification"] == "not applicable"

    def test_receipt_never_claims_verified_without_a_verify_call(self, conn, case):
        """An action that COMPLETED but was never re-checked must show
        'unverified', never be silently promoted to 'confirmed'."""
        from agentx import capabilities as caps
        from agentx.execution import runner
        runner.run(conn, case=case, action="request_refund",
                  params={"counterparty": "Kartly", "amount_minor": 5000,
                          "currency": "INR", "case_id": case["id"]},
                  capability=caps.get("refund_request"))
        env = receipt.issue(conn, case["id"])
        assert env["receipt"]["readable"]["verification"] in ("unverified", "not applicable")
        assert env["receipt"]["readable"]["verification"] != "confirmed"

    def test_persisted_receipt_is_served_byte_identical(self, conn, case):
        env1 = receipt.issue(conn, case["id"])
        env2 = receipt.latest(conn, case["id"])
        assert env1["sha256"] == env2["sha256"]


class TestEvidencePackage:
    def test_package_traces_every_fact_to_evidence(self, conn, case):
        from agentx.evidence import extract, package as pkg
        ev = egraph.add_evidence(conn, case_id=case["id"], workspace="default",
                                 subject=case["subject"], kind="receipt",
                                 text="Total: 100.00 GBP")
        facts = extract.extract("Total: 100.00 GBP", "receipt", use_llm=False)
        egraph.add_facts(conn, case["id"], ev["id"], facts)
        body = pkg.build(conn, case["id"], audience="human_review")
        for f in body["facts"]:
            assert f["sources"], f"fact {f['predicate']} has no source"

    def test_package_lists_open_contradictions_rather_than_hiding_them(self, conn, case):
        from agentx.evidence import contradiction, extract, package as pkg
        ev1 = egraph.add_evidence(conn, case_id=case["id"], workspace="default",
                                  subject=case["subject"], kind="receipt", text="a")
        ev2 = egraph.add_evidence(conn, case_id=case["id"], workspace="default",
                                  subject=case["subject"], kind="invoice", text="b")
        egraph.add_facts(conn, case["id"], ev1["id"], [
            extract.FactCandidate(predicate="order.total", value_text="100",
                                  value_num=10000, confidence=0.9)])
        egraph.add_facts(conn, case["id"], ev2["id"], [
            extract.FactCandidate(predicate="order.total", value_text="200",
                                  value_num=20000, confidence=0.9)])
        contradiction.detect(conn, case["id"])
        body = pkg.build(conn, case["id"], audience="payment_dispute")
        assert body["contradictions"], "package must surface open contradictions"


class TestLetterGrounding:
    def test_deterministic_draft_only_cites_established_facts(self, conn, case):
        from agentx import letters
        ev = egraph.add_evidence(conn, case_id=case["id"], workspace="default",
                                 subject=case["subject"], kind="transaction",
                                 text="Total charged: 2,399.00 INR")
        from agentx.evidence import extract
        facts = extract.extract("Total charged: 2,399.00 INR", "transaction",
                                use_llm=False)
        egraph.add_facts(conn, case["id"], ev["id"], facts)
        body, subject = letters.compose(conn, case, {
            "remedy": "merchant_refund", "counterparty": "Kartly",
            "amount_minor": 239900, "currency": "INR"})
        assert "2,399.00" in body or "₹2,399.00" in body

    def test_grounding_rejects_an_invented_amount(self, conn, case):
        from agentx import letters
        # A rewrite that introduces a figure nowhere in the fact graph must fail
        # the grounding check.
        fake = "I am owed a refund of £999,999.00 immediately under reference ZZ999888."
        report = letters.grounding_report(fake, conn, case["id"])
        assert not report["grounded"]

    def test_grounding_accepts_text_with_only_known_figures(self, conn, case):
        from agentx import letters
        from agentx.evidence import extract
        ev = egraph.add_evidence(conn, case_id=case["id"], workspace="default",
                                 subject=case["subject"], kind="transaction",
                                 text="Total charged: 50.00 GBP")
        facts = extract.extract("Total charged: 50.00 GBP", "transaction", use_llm=False)
        egraph.add_facts(conn, case["id"], ev["id"], facts)
        text = "Please refund 50.00 GBP to my account. Case " + case["id"] + "."
        report = letters.grounding_report(text, conn, case["id"])
        assert report["grounded"]
