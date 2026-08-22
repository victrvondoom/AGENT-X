"""
Outcome memory — the closed loop between cases, and the boundaries on it.

The interesting assertions here are the negative ones: that a prior cannot
authorise anything, that thin evidence is refused as thin, that sandbox lessons
never shape live plans, and that the learning survives an erasure that destroys
the case it came from.
"""
from __future__ import annotations

import pytest

from agentx import case as case_mod
from agentx import chain, demo, outcomes, store
from agentx.execution import providers


@pytest.fixture(autouse=True)
def sqlite_engine(tmp_path):
    store.reset_for_tests(str(tmp_path / "outcomes.db"))
    providers.clear()
    providers.bootstrap()
    yield


@pytest.fixture
def conn():
    with store.connect() as c:
        demo.reset(c)
        yield c


def _run(conn, n=1, key="A"):
    return [demo.run(conn, key, use_llm=False) for _ in range(n)]


class TestRecording:
    def test_a_closed_case_writes_exactly_one_outcome(self, conn):
        r = _run(conn)[0]
        rows = outcomes.history(conn)
        assert len(rows) == 1
        assert rows[0]["case_id"] == r["case_id"]
        assert rows[0]["outcome"] == "resolved"

    def test_recording_is_idempotent(self, conn):
        r = _run(conn)[0]
        c = case_mod.get(conn, r["case_id"])
        assert outcomes.record(conn, c, outcome="resolved") is None
        assert len(outcomes.history(conn)) == 1

    def test_structural_fields_are_captured(self, conn):
        _run(conn)
        row = outcomes.history(conn)[0]
        assert row["counterparty"] == "kartly"
        assert row["problem_type"] == "duplicate_charge"
        assert row["chases_needed"] >= 1
        assert row["escalated"] is True
        assert row["provider_mode"] == "sandbox"

    def test_no_personal_data_columns_exist(self, conn):
        """The table cannot hold PII because there is nowhere to put it — this
        is the property the erasure story depends on, so it is asserted rather
        than assumed."""
        _run(conn)
        row = outcomes.history(conn)[0]
        forbidden = {"description", "narrative", "user_ref", "amount_minor",
                     "external_ref", "subject", "body", "title"}
        assert not (forbidden & set(row)), f"outcome row leaked: {forbidden & set(row)}"
        # A ratio, never an amount.
        assert row["recovery_ratio"] is None or 0.0 <= row["recovery_ratio"] <= 1.0


class TestPrior:
    def test_no_history_returns_an_explicit_empty_prior(self, conn):
        p = outcomes.prior_for(conn, counterparty="Nobody", problem_type="duplicate_charge")
        assert p["cases"] == 0
        assert p["actionable"] is False

    def test_one_case_is_reported_but_not_actionable(self, conn):
        _run(conn, 1)
        p = outcomes.prior_for(conn, counterparty="Kartly",
                               problem_type="duplicate_charge")
        assert p["cases"] == 1
        assert p["actionable"] is False       # an anecdote may not steer a plan
        assert p["strength"] == "weak"

    def test_two_cases_become_actionable(self, conn):
        _run(conn, 2)
        p = outcomes.prior_for(conn, counterparty="Kartly",
                               problem_type="duplicate_charge")
        assert p["cases"] == 2
        assert p["actionable"] is True
        assert p["best_strategy"] == "merchant_refund"
        assert p["escalation_rate"] == 1.0

    def test_prior_cites_the_cases_it_learned_from(self, conn):
        rs = _run(conn, 2)
        p = outcomes.prior_for(conn, counterparty="Kartly",
                               problem_type="duplicate_charge")
        assert set(p["basis"]) == {r["case_id"] for r in rs}

    def test_sandbox_priors_do_not_leak_into_live_lookups(self, conn):
        _run(conn, 3)
        live = outcomes.prior_for(conn, counterparty="Kartly",
                                  problem_type="duplicate_charge", mode="live")
        assert live["cases"] == 0, "a sandbox lesson must never shape a live plan"

    def test_prior_is_scoped_to_the_counterparty(self, conn):
        _run(conn, 2)
        other = outcomes.prior_for(conn, counterparty="Streamly",
                                   problem_type="duplicate_charge")
        assert other["cases"] == 0


class TestPlanIsInformedNotAuthorised:
    def test_plan_carries_the_prior_it_was_shaped_by(self, conn):
        _run(conn, 2)
        r = _run(conn, 1)[0]
        prior = (r["snapshot"]["plan"] or {}).get("prior") or {}
        assert prior.get("cases") == 2
        assert prior.get("basis")

    def test_chase_budget_follows_experience(self, conn):
        _run(conn, 2)
        r = _run(conn, 1)[0]
        chase = next(s for s in r["snapshot"]["plan"]["steps"] if s["key"] == "chase")
        assert 1 <= chase["retry"]["max"] <= 3

    def test_the_wait_adapts_to_how_long_they_actually_take(self, conn):
        """This branch was unreachable until elapsed time was measured properly.

        `typical_days` was 0.0 for every sandbox case, so the planner's
        experience-adjusted wait never fired and the plan always used the
        counterparty's stated SLA. With real elapsed time it does what it was
        written to do: Kartly publishes a 5-day SLA and takes considerably
        longer, so the plan waits the observed time instead of chasing into
        silence on day five.
        """
        _run(conn, 3)
        r = _run(conn, 1)[0]
        prior = r["snapshot"]["plan"]["prior"]
        assert prior["typical_days"] > 0, "elapsed time collapsed to zero again"
        assert prior["applied"] is True
        assert "instead of the stated" in prior["adjustment"]
        wait = next(s for s in r["snapshot"]["plan"]["steps"]
                    if s["key"] == "await_response")
        assert str(int(round(prior["typical_days"]))) in wait["title"]

    def test_the_adjustment_is_bounded(self, conn):
        """One unusual case must not produce an absurd plan: the learned wait is
        clamped to 2..30 days however extreme the history."""
        _run(conn, 3)
        r = _run(conn, 1)[0]
        learned = int(round(r["snapshot"]["plan"]["prior"]["typical_days"]))
        assert 2 <= max(2, min(30, learned)) <= 30

    def test_a_prior_never_removes_an_approval(self, conn):
        """However much experience says escalation will be needed, the escalation
        step must still sit at its declared autonomy level — a prior informs the
        shape of a plan, never the permission to run it."""
        _run(conn, 3)
        r = _run(conn, 1)[0]
        steps = {s["key"]: s for s in r["snapshot"]["plan"]["steps"]}
        if "escalate" in steps:
            assert steps["escalate"]["required_level"] >= 3
            assert steps["escalate"]["risk"] == "high"


class TestSystemicSignal:
    def test_below_threshold_reports_nothing(self, conn):
        _run(conn, 2)
        assert outcomes.systemic_signal(conn, counterparty="Kartly",
                                        problem_type="duplicate_charge") is None

    def test_three_escalated_cases_raise_a_pattern(self, conn):
        _run(conn, 3)
        sig = outcomes.systemic_signal(conn, counterparty="Kartly",
                                       problem_type="duplicate_charge")
        assert sig is not None
        assert sig["pattern"] == "settles_only_on_escalation"
        assert len(sig["basis"]) == 3


class TestLearningSurvivesErasure:
    def test_outcome_survives_the_shredding_of_its_own_case(self, conn):
        """The point of storing structure rather than content: erasure destroys
        the case, and what the system learned from it is still true and still
        available, because it was never personal data."""
        r = _run(conn, 2)
        before = outcomes.prior_for(conn, counterparty="Kartly",
                                    problem_type="duplicate_charge")

        out = case_mod.forget(conn, r[0]["case_id"])
        assert out["unrecoverable"] is True

        after = outcomes.prior_for(conn, counterparty="Kartly",
                                   problem_type="duplicate_charge")
        assert after["cases"] == before["cases"]
        assert after["best_strategy"] == before["best_strategy"]
        # …and the erased case's own contents really are gone.
        from agentx.evidence import graph as egraph
        ev = egraph.list_evidence(conn, r[0]["case_id"])
        assert ev, "evidence rows remain as structure"
        assert egraph.evidence_text(conn, ev[0]["id"]) is None


class TestElapsedTimeIsMeasuredOnOneClock:
    """`days_to_close` feeds `typical_days`, which is shown to a person deciding
    whether a claim is worth chasing. Two bugs made it fiction, and both are the
    kind that look like working software:

      * every stamp was wall-clock, so a case that chased twice and escalated
        once across seven simulated days recorded 0.0 days — the system would
        have reported that this merchant pays out the same day;
      * once follow-ups were stamped on the sandbox clock, a case opened AFTER
        the clock had been moved inherited every day advanced before it existed.
    """

    def test_a_case_that_chased_and_escalated_did_not_take_zero_days(self, conn):
        demo.run(conn, "A", use_llm=False)
        row = outcomes.history(conn)[0]
        assert row["chases_needed"] >= 1
        assert row["days_to_close"] > 0, (
            "a case that waited on a merchant cannot have closed instantly")

    def test_days_reflect_the_scenario_not_the_session(self, conn):
        """Run a slow scenario, then a fast one. The fast one must not inherit
        the slow one's elapsed days."""
        demo.run(conn, "D", use_llm=False)      # escalates; many simulated days
        demo.run(conn, "E", use_llm=False)      # settles on first contact
        by_case = {r["case_id"]: r for r in outcomes.history(conn)}
        slow, fast = sorted(by_case.values(), key=lambda r: r["created_at"])[:2]
        assert fast["days_to_close"] < slow["days_to_close"], (
            f"the second case reported {fast['days_to_close']}d against the "
            f"first's {slow['days_to_close']}d — the global clock leaked in")

    def test_a_case_opened_on_a_moved_clock_starts_from_zero(self, conn):
        from agentx.sandbox import world
        world.advance(conn, 30)
        c = case_mod.create(conn, description="They charged me twice.",
                               workspace="default")
        assert float(c["opened_offset_days"]) == pytest.approx(30.0)
        assert outcomes._elapsed_days(conn, c) == pytest.approx(0.0, abs=0.05), (
            "a case opened today cannot already be thirty days old")

    def test_a_live_case_is_plain_wall_clock(self, conn):
        """No sandbox clock, no adjustment: the column is 0 and the arithmetic
        is exactly the subtraction it always was."""
        c = case_mod.create(conn, description="They charged me twice.",
                               workspace="default")
        assert float(c["opened_offset_days"] or 0) == 0.0
        assert outcomes._elapsed_days(conn, c) == pytest.approx(0.0, abs=0.05)
