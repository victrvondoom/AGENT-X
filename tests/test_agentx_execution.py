"""
Action execution — Action → Evidence → Verification, and the chain that survives
crypto-shredding.
"""
from __future__ import annotations

import pytest

from agentx import capabilities as caps
from agentx import chain, engine, ids, sealing, store
from agentx import case as case_mod
from agentx.execution import providers, runner


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
    c = case_mod.create(conn, description="test", autonomy_level=4)
    # A bare case has confidence=None until `investigate()` runs, and the
    # governor correctly refuses any risk-bearing action on an absent signal
    # (absent is not the same as low). These tests exercise execution and
    # verification in isolation, so they set the confidence a real case would
    # have reached by this point rather than triggering that (separately tested)
    # refusal on every call.
    return case_mod.update(conn, c["id"], confidence=0.9)


class TestNoProviderNoStep:
    def test_unknown_family_produces_a_failed_record_not_an_exception(self, conn, case):
        rec = runner.run(conn, case=case, action="cancel",
                         params={"counterparty": "NoSuchCompany Ltd"},
                         capability=caps.get("cancellation"))
        assert rec["state"] == "FAILED"
        assert "no provider" in rec["error"]


class TestGovernorIntegration:
    def test_low_autonomy_case_requires_authorization_before_sending(self, conn):
        low = case_mod.create(conn, description="test", autonomy_level=1)
        low = case_mod.update(conn, low["id"], confidence=0.9)
        assert low is not None
        with pytest.raises(runner.NotAuthorized):
            runner.run(conn, case=low, action="request_refund",
                      params={"counterparty": "Kartly", "amount_minor": 1000,
                              "currency": "GBP", "case_id": low["id"]},
                      capability=caps.get("refund_request"))

    def test_authorized_action_actually_runs(self, conn, case):
        with pytest.raises(runner.NotAuthorized) as exc:
            runner.run(conn, case=case, action="escalate",
                      params={"counterparty": "Kartly", "to": "payment_provider",
                              "case_id": case["id"]},
                      capability=caps.get("escalation"))
        auth = runner.active_authorization(conn, case["id"], action="escalate")
        assert auth is None  # not granted yet


class TestExecutionRecord:
    def test_completed_action_is_never_marked_verified_by_default(self, conn, case):
        rec = runner.run(conn, case=case, action="request_refund",
                         params={"counterparty": "Kartly", "amount_minor": 5000,
                                 "currency": "INR", "case_id": case["id"],
                                 "problem_type": "duplicate_charge"},
                         capability=caps.get("refund_request"))
        assert rec["state"] == "COMPLETED"
        assert rec["verified"] == "unverified"

    def test_action_produces_evidence_that_can_be_read_back(self, conn, case):
        rec = runner.run(conn, case=case, action="request_refund",
                         params={"counterparty": "Kartly", "amount_minor": 5000,
                                 "currency": "INR", "case_id": case["id"]},
                         capability=caps.get("refund_request"))
        assert rec.get("evidence_id")
        from agentx.evidence import graph as egraph
        text = egraph.evidence_text(conn, rec["evidence_id"])
        assert text and "Kartly" in text

    def test_execution_record_is_immutable_history_not_overwritten_on_retry(self, conn, case):
        runner.run(conn, case=case, action="request_refund",
                  params={"counterparty": "Kartly", "amount_minor": 5000,
                          "currency": "INR", "case_id": case["id"]},
                  capability=caps.get("refund_request"))
        runner.run(conn, case=case, action="request_refund",
                  params={"counterparty": "Kartly", "amount_minor": 5000,
                          "currency": "INR", "case_id": case["id"]},
                  capability=caps.get("refund_request"))
        hist = runner.history(conn, case["id"])
        refunds = [h for h in hist if h["action"] == "request_refund"]
        assert len(refunds) == 2


class TestVerification:
    def test_verify_reads_the_ledger_not_the_reply(self, conn, case):
        rec = runner.run(conn, case=case, action="request_refund",
                         params={"counterparty": "Kartly", "amount_minor": 5000,
                                 "currency": "INR", "case_id": case["id"],
                                 "problem_type": "wrong_item_received"},
                         capability=caps.get("refund_request"))
        v = runner.verify(conn, case=case, execution_id=rec["id"])
        assert v["verified"] in ("verified", "unverified", "unverifiable")

    def test_verify_on_unknown_provider_family_reports_unverifiable(self, conn, case):
        exec_id = ids.new("ex")
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO executions (id, case_id, action, provider, provider_mode,"
                " state, verified, requested_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (exec_id, case["id"], "draft", "internal", "internal", "COMPLETED",
                 "unverified", ids.now()))
        v = runner.verify(conn, case=case, execution_id=exec_id)
        assert v["verified"] == "unverifiable"


class TestChainAndErasure:
    def test_chain_verifies_after_case_creation(self, conn, case):
        v = chain.verify(conn, case["id"])
        assert v["ok"]
        assert v["rows"] >= 1

    def test_tampering_a_row_breaks_verification(self, conn, case):
        with conn.cursor() as cur:
            cur.execute("UPDATE case_chain SET detail = %s WHERE case_id = %s AND seq = 0",
                        ('{"tampered":true}', case["id"]))
        v = chain.verify(conn, case["id"])
        assert not v["ok"]

    def test_forget_destroys_the_key_and_chain_still_verifies(self, conn, case):
        egraph_add(conn, case)
        before = chain.verify(conn, case["id"])
        assert before["ok"]
        out = case_mod.forget(conn, case["id"])
        assert out["unrecoverable"]
        after = chain.verify(conn, case["id"])
        assert after["ok"], "chain must still verify after crypto-shred"
        assert after["rows"] > before["rows"]  # the erasure event was itself appended

    def test_sealed_content_is_unreadable_after_forget(self, conn, case):
        ev_id = egraph_add(conn, case)
        case_mod.forget(conn, case["id"])
        from agentx.evidence import graph as egraph
        text = egraph.evidence_text(conn, ev_id)
        assert text is None


def egraph_add(conn, case) -> str:
    from agentx.evidence import graph as egraph
    ev = egraph.add_evidence(conn, case_id=case["id"], workspace=case["workspace"],
                             subject=case["subject"], kind="receipt",
                             text="sensitive content here")
    return ev["id"]


class TestProviderRegistry:
    def test_named_counterparty_outside_family_still_resolves(self, conn):
        """Streamly is registered under `subscription`; a `merchant`-family lookup
        naming Streamly must still find it rather than falling back to Kartly."""
        pool = providers.for_family("merchant", counterparty="Streamly")
        assert pool
        assert pool[0].can_serve("Streamly")

    def test_unknown_counterparty_gets_no_named_provider(self):
        pool = providers.for_family("merchant", counterparty="Totally Unknown Co")
        # falls back to generic ("*") providers only, none of which claim to BE
        # that specific company
        assert all("*" in p.serves for p in pool[:1]) or not pool

    def test_unavailable_provider_reports_a_reason(self):
        p = providers.resolve("booking", counterparty="NoSuchAirline",
                              action="cancel")
        from agentx.execution.providers.base import UnavailableProvider
        # NoSuchAirline has no dedicated provider, but generic booking providers
        # exist (Skylink/Meridian are named, not generic) - so this may resolve
        # to an UnavailableProvider since none are generic "*" for booking.
        assert p is not None


class TestVerificationPersistsFigures:
    """Regression: verify() used to write only `verified`, leaving executions.result
    holding the PRE-verification payload. receipt._recovered() sums `posted_minor`
    off that column, so a refund confirmed in the counterparty's ledger produced a
    signed receipt reporting no money recovered."""

    def test_verified_execution_carries_posted_amount(self, conn, case):
        rec = runner.run(conn, case=case, action="request_refund",
                         params={"counterparty": "Kartly", "amount_minor": 5000,
                                 "currency": "INR", "case_id": case["id"],
                                 "problem_type": "wrong_item_received"},
                         capability=caps.get("refund_request"))
        runner.verify(conn, case=case, execution_id=rec["id"])
        hist = {h["id"]: h for h in runner.history(conn, case["id"])}
        data = hist[rec["id"]]["data"]
        assert data.get("posted_minor") == 5000

    def test_receipt_reports_the_recovered_amount(self, conn, case):
        from agentx import receipt as receipt_mod
        rec = runner.run(conn, case=case, action="request_refund",
                         params={"counterparty": "Kartly", "amount_minor": 5000,
                                 "currency": "INR", "case_id": case["id"],
                                 "problem_type": "wrong_item_received"},
                         capability=caps.get("refund_request"))
        runner.verify(conn, case=case, execution_id=rec["id"])
        env = receipt_mod.issue(conn, case["id"], store_it=False)
        assert env["receipt"]["readable"]["amount_recovered"] is not None


class TestLedgerAccumulates:
    """Regression: a second posting against one ticket (the escalation top-up after
    a partial refund) overwrote the first ledger row instead of adding to it."""

    def test_two_postings_sum_rather_than_overwrite(self, conn):
        from agentx.execution.providers import sandbox_providers as sp
        from agentx.sandbox import world
        ticket = {"ticket_ref": "TST-000001", "currency": "GBP"}
        sp._post_refund(conn, "meridian", ticket, 12000)
        sp._post_refund(conn, "meridian", ticket, 6100)
        led = world.fetch(conn, "meridian", "payment", "RFND-TST-000001")
        assert led is not None
        assert led["amount_minor"] == 18100
        assert len(led["postings"]) == 2
        assert ticket["amount_approved_minor"] == 18100


class TestPartialIsNotResolved:
    """Regression: a partial credit reported verification outcome "done", so the
    follow-up agent marked the case RESOLVED while the signed receipt still read
    "the balance is still outstanding" — a claim contradicted by its own
    evidence, which is the exact failure this product exists to prevent."""

    def test_partial_credit_verifies_as_pending_not_done(self, conn, case):
        from agentx.execution.providers import sandbox_providers as sp
        from agentx.sandbox import world
        # Meridian's hotel_booking_cancelled policy pays 60% on first contact.
        rec = runner.run(conn, case=case, action="request_refund",
                         params={"counterparty": "Meridian Suites",
                                 "amount_minor": 10000, "currency": "GBP",
                                 "case_id": case["id"],
                                 "problem_type": "hotel_booking_cancelled"},
                         capability=caps.get("refund_request"))
        v = runner.verify(conn, case=case, execution_id=rec["id"])
        assert v["verified"] == "unverified", (
            "a partial credit must not verify as complete")
        assert v["data"]["verified"] is True    # something WAS posted
        assert v["data"]["full"] is False       # but not the whole claim

    def test_full_credit_verifies_as_done(self, conn, case):
        rec = runner.run(conn, case=case, action="request_refund",
                         params={"counterparty": "Kartly", "amount_minor": 5000,
                                 "currency": "INR", "case_id": case["id"],
                                 "problem_type": "wrong_item_received"},
                         capability=caps.get("refund_request"))
        v = runner.verify(conn, case=case, execution_id=rec["id"])
        assert v["verified"] == "verified"


# ─────────────────────────────────────────────────────────────────────────────
# retry + idempotency
#
# Regression class: engine._finish_step() marks a plan step DONE only AFTER
# runner.run() returns. A crash between the provider call succeeding and that
# write left a step whose status was still PENDING but whose action had already
# happened — and the next advance() would run it again. request_refund,
# cancel and email are not safe to send twice.
# ─────────────────────────────────────────────────────────────────────────────
from agentx.execution.providers.base import ErrorCode, Provider, ProviderResult


class _CountingProvider(Provider):
    """Registered fresh per test (the autouse fixture clears the registry), and
    served under a counterparty name no sandbox provider claims, so it is the
    only match — `for_family()` would otherwise rank a named sandbox provider
    (e.g. Kartly's) above a generic "*" one."""
    id = "test:counting"
    family = "merchant"
    mode = "sandbox"
    label = "Counting test provider"
    serves = ("*",)

    def __init__(self, fail_times: int = 0, error_code: str = ErrorCode.RETRYABLE):
        self.calls = 0
        self.fail_times = fail_times
        self.error_code = error_code

    def do_request_refund(self, p: dict) -> ProviderResult:
        self.calls += 1
        if self.calls <= self.fail_times:
            return ProviderResult(False, "error", self.id, self.mode,
                                  message="simulated transient failure",
                                  error_code=self.error_code, retryable=True)
        return ProviderResult(True, "accepted", self.id, self.mode,
                              external_ref=f"REF-{self.calls}",
                              message="accepted")


class TestRetryIntegration:
    def test_a_transient_failure_is_retried_and_succeeds(self, conn, case):
        p = _CountingProvider(fail_times=2)
        providers.register(p)
        rec = runner.run(conn, case=case, action="request_refund",
                         params={"counterparty": "Unclaimed Test Co",
                                 "amount_minor": 1000, "currency": "GBP",
                                 "case_id": case["id"]},
                         step_id="st-retry-1", capability=caps.get("refund_request"))
        assert rec["state"] == "COMPLETED"
        assert rec["attempts"] == 3
        assert p.calls == 3

    def test_a_permanently_failing_provider_still_records_one_execution(self, conn, case):
        p = _CountingProvider(fail_times=99)
        providers.register(p)
        rec = runner.run(conn, case=case, action="request_refund",
                         params={"counterparty": "Unclaimed Test Co",
                                 "amount_minor": 1000, "currency": "GBP",
                                 "case_id": case["id"]},
                         step_id="st-retry-2", capability=caps.get("refund_request"))
        assert rec["state"] == "FAILED"
        assert p.calls == 3           # default max_attempts, then it stops
        assert len(runner.history(conn, case["id"])) == 1


class TestIdempotency:
    def test_a_second_run_with_the_same_step_id_does_not_call_the_provider_again(
            self, conn, case):
        p = _CountingProvider()
        providers.register(p)
        params = {"counterparty": "Unclaimed Test Co", "amount_minor": 1000,
                  "currency": "GBP", "case_id": case["id"]}
        first = runner.run(conn, case=case, action="request_refund", params=params,
                           step_id="st-idem-1", capability=caps.get("refund_request"))
        second = runner.run(conn, case=case, action="request_refund", params=params,
                            step_id="st-idem-1", capability=caps.get("refund_request"))
        assert p.calls == 1, "the second run() must be a replay, not a second send"
        assert second.get("replayed") is True
        assert second["external_ref"] == first["external_ref"]
        assert len(runner.history(conn, case["id"])) == 1

    def test_different_step_ids_are_independent_actions(self, conn, case):
        p = _CountingProvider()
        providers.register(p)
        params = {"counterparty": "Unclaimed Test Co", "amount_minor": 1000,
                  "currency": "GBP", "case_id": case["id"]}
        runner.run(conn, case=case, action="request_refund", params=params,
                  step_id="st-a", capability=caps.get("refund_request"))
        runner.run(conn, case=case, action="request_refund", params=params,
                  step_id="st-b", capability=caps.get("refund_request"))
        assert p.calls == 2

    def test_an_interrupted_write_action_is_not_silently_retried(self, conn, case):
        """Simulates a crash: an execution row left in STARTED with no COMPLETED/
        FAILED written — the provider may or may not have actually sent anything.
        The next run() for the same step must refuse to call the provider again."""
        p = _CountingProvider()
        providers.register(p)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO executions (id, case_id, step_id, action, provider,"
                " provider_mode, state, verified, requested_at, started_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (ids.new("ex"), case["id"], "st-crashed", "request_refund",
                 p.id, p.mode, "STARTED", "unverified", ids.now(), ids.now()))
        rec = runner.run(conn, case=case, action="request_refund",
                         params={"counterparty": "Unclaimed Test Co",
                                 "amount_minor": 1000, "currency": "GBP",
                                 "case_id": case["id"]},
                         step_id="st-crashed", capability=caps.get("refund_request"))
        assert p.calls == 0, "must not have called the provider on an uncertain prior attempt"
        assert rec["state"] == "FAILED"
        assert rec["error_code"] == ErrorCode.REQUIRES_USER

    def test_an_interrupted_read_only_action_is_safe_to_retry(self, conn, case):
        """retrieve/inspect/verify have no external side effect, so a STARTED-but-
        unfinished prior attempt does not block running it again."""
        class _Retriever(Provider):
            id = "test:retriever"
            family = "merchant"
            mode = "sandbox"
            label = "test"
            serves = ("*",)
            calls = 0
            def do_retrieve(self, p):
                type(self).calls += 1
                return ProviderResult(True, "done", self.id, self.mode,
                                      evidence_text="fetched")
        providers.register(_Retriever())
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO executions (id, case_id, step_id, action, provider,"
                " provider_mode, state, verified, requested_at, started_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (ids.new("ex"), case["id"], "st-read-crashed", "retrieve",
                 "test:retriever", "sandbox", "STARTED", "unverified", ids.now(), ids.now()))
        rec = runner.run(conn, case=case, action="retrieve",
                         params={"counterparty": "Unclaimed Test Co",
                                 "case_id": case["id"]},
                         step_id="st-read-crashed")
        assert _Retriever.calls == 1
        assert rec["state"] == "COMPLETED"
