"""
Evidence Intelligence — the no-fact-without-a-link rule, and contradictions that
refuse to be smoothed away.
"""
from __future__ import annotations

import pytest

from agentx import normalize, store
from agentx import case as case_mod
from agentx.evidence import contradiction, extract, graph as egraph


@pytest.fixture(autouse=True)
def sqlite_engine(tmp_path):
    store.reset_for_tests(str(tmp_path / "agentx_test.db"))
    yield
    store.reset_for_tests(str(tmp_path / "agentx_test.db"))


@pytest.fixture
def conn():
    with store.connect() as c:
        yield c


@pytest.fixture
def case(conn):
    return case_mod.create(conn, description="test case for evidence")


class TestNormalize:
    def test_money_reads_symbol_and_amount(self):
        m = normalize.money("Total: ₹2,399.00")
        assert m["minor"] == 239900
        assert m["currency"] == "INR"

    def test_money_returns_none_currency_when_absent(self):
        m = normalize.money("2399.00")
        assert m is not None
        assert m["currency"] is None

    def test_zero_decimal_currency_does_not_multiply_by_100(self):
        m = normalize.money("¥5000")
        assert m["currency"] == "JPY"
        assert m["minor"] == 5000

    def test_ambiguous_numeric_date_is_flagged(self):
        d = normalize.date("Date: 03/04/2026")
        assert d["ambiguous"] is True

    def test_unambiguous_numeric_date_not_flagged(self):
        d = normalize.date("Date: 25/03/2026")
        assert d["ambiguous"] is False
        assert d["iso"] == "2026-03-25"

    def test_iso_date_parses_directly(self):
        d = normalize.date("2026-08-02")
        assert d["iso"] == "2026-08-02"

    def test_reference_extraction_skips_generic_words(self):
        refs = normalize.references("Order  Total charged 2,399")
        values = [r["value"] for r in refs]
        assert "TOTAL" not in values

    def test_reference_extraction_finds_real_order_id(self):
        refs = normalize.references("Order: 402-9938271")
        assert any(r["value"] == "402-9938271" for r in refs)

    def test_fmt_money_never_invents_a_currency(self):
        assert normalize.fmt_money(1000, None) == "10.00"
        assert "₹" not in normalize.fmt_money(1000, None)


class TestExtraction:
    def test_deterministic_extraction_reads_amount_and_date(self):
        facts = extract.extract(
            "Total charged: 2,399.00 INR\nDate: 2026-08-02", "transaction",
            use_llm=False)
        preds = {f.predicate for f in facts}
        assert "charge.amount" in preds
        assert "charge.date" in preds

    def test_every_fact_has_a_method_and_confidence(self):
        facts = extract.extract("Total: 100.00 GBP", "receipt", use_llm=False)
        for f in facts:
            assert f.method == "deterministic"
            assert 0.0 <= f.confidence <= 1.0

    def test_gate_routes_low_confidence_to_human(self):
        facts = extract.extract("random text with 999", "receipt", use_llm=False)
        summary = extract.gate_summary(facts)
        assert "total" in summary


class TestFactGraph:
    def test_no_fact_without_a_link(self, conn, case):
        ev = egraph.add_evidence(conn, case_id=case["id"], workspace="default",
                                 subject=case["subject"], kind="receipt",
                                 text="Total: 100.00 GBP", filename="r.txt")
        facts = extract.extract("Total: 100.00 GBP", "receipt", use_llm=False)
        written = egraph.add_facts(conn, case["id"], ev["id"], facts)
        for f in written:
            links = egraph.links_for(conn, [f["id"]])
            assert links.get(f["id"]), f"fact {f['id']} has no evidence link"

    def test_evidence_hash_is_of_raw_bytes(self, conn, case):
        import hashlib
        text = "Total: 50.00 GBP"
        ev = egraph.add_evidence(conn, case_id=case["id"], workspace="default",
                                 subject=case["subject"], kind="receipt", text=text)
        assert ev["sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()

    def test_seal_and_unseal_roundtrip(self, conn, case):
        ev = egraph.add_evidence(conn, case_id=case["id"], workspace="default",
                                 subject=case["subject"], kind="receipt",
                                 text="secret content")
        text = egraph.evidence_text(conn, ev["id"])
        assert text == "secret content"

    def test_claim_confidence_uses_noisy_or_not_max(self, conn, case):
        ev1 = egraph.add_evidence(conn, case_id=case["id"], workspace="default",
                                  subject=case["subject"], kind="receipt", text="a")
        ev2 = egraph.add_evidence(conn, case_id=case["id"], workspace="default",
                                  subject=case["subject"], kind="invoice", text="b")
        egraph.add_facts(conn, case["id"], ev1["id"], [
            extract.FactCandidate(predicate="charge.amount", value_text="100",
                                  value_num=100, confidence=0.7)])
        egraph.add_facts(conn, case["id"], ev2["id"], [
            extract.FactCandidate(predicate="charge.amount", value_text="100",
                                  value_num=100, confidence=0.7)])
        claim = egraph.build_claim(conn, case["id"], "charge.amount", "test claim")
        assert claim.confidence > 0.7  # two sources agreeing beats either alone
        assert claim.confidence < 1.0  # never certain


class TestContradictions:
    def test_disagreeing_amounts_are_flagged_not_averaged(self, conn, case):
        ev1 = egraph.add_evidence(conn, case_id=case["id"], workspace="default",
                                  subject=case["subject"], kind="receipt",
                                  text="Total: 2399.00 GBP")
        ev2 = egraph.add_evidence(conn, case_id=case["id"], workspace="default",
                                  subject=case["subject"], kind="bank_statement",
                                  text="Total: 2499.00 GBP")
        egraph.add_facts(conn, case["id"], ev1["id"], [
            extract.FactCandidate(predicate="charge.amount", value_text="2399.00",
                                  value_num=239900, unit="GBP", confidence=0.9)])
        egraph.add_facts(conn, case["id"], ev2["id"], [
            extract.FactCandidate(predicate="charge.amount", value_text="2499.00",
                                  value_num=249900, unit="GBP", confidence=0.9)])
        found = contradiction.detect(conn, case["id"])
        assert len(found) == 1
        assert found[0]["severity"] == "blocking"  # both issuer_document

    def test_two_issuer_documents_disagreeing_is_blocking(self, conn, case):
        ev1 = egraph.add_evidence(conn, case_id=case["id"], workspace="default",
                                  subject=case["subject"], kind="receipt", text="a",
                                  trust="issuer_document")
        ev2 = egraph.add_evidence(conn, case_id=case["id"], workspace="default",
                                  subject=case["subject"], kind="invoice", text="b",
                                  trust="issuer_document")
        egraph.add_facts(conn, case["id"], ev1["id"], [
            extract.FactCandidate(predicate="order.total", value_text="100",
                                  value_num=10000, confidence=0.9)])
        egraph.add_facts(conn, case["id"], ev2["id"], [
            extract.FactCandidate(predicate="order.total", value_text="200",
                                  value_num=20000, confidence=0.9)])
        found = contradiction.detect(conn, case["id"])
        assert found[0]["severity"] == "blocking"

    def test_negligible_difference_is_not_a_contradiction(self, conn, case):
        ev1 = egraph.add_evidence(conn, case_id=case["id"], workspace="default",
                                  subject=case["subject"], kind="receipt", text="a")
        ev2 = egraph.add_evidence(conn, case_id=case["id"], workspace="default",
                                  subject=case["subject"], kind="invoice", text="b")
        egraph.add_facts(conn, case["id"], ev1["id"], [
            extract.FactCandidate(predicate="flight.delay_minutes", value_text="240",
                                  value_num=240, confidence=0.9)])
        egraph.add_facts(conn, case["id"], ev2["id"], [
            extract.FactCandidate(predicate="flight.delay_minutes", value_text="241",
                                  value_num=241, confidence=0.9)])
        found = contradiction.detect(conn, case["id"])
        assert found == []

    def test_explaining_a_contradiction_records_the_reason(self, conn, case):
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
        found = contradiction.detect(conn, case["id"])
        out = contradiction.explain(conn, found[0]["id"], "the second includes a fee")
        assert out["resolution"] == "the second includes a fee"
        assert contradiction.open_contradictions(conn, case["id"]) == []
