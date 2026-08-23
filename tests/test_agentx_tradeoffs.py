"""
Pareto trade-offs — narrowing a choice without making it.

The property that matters is not "the frontier is computed correctly" but that
the module never quietly decides for the user: two routes that are genuinely
different must BOTH survive, and a route that is strictly worse must be removed
with the winner named rather than silently dropped.
"""
from __future__ import annotations

import pytest

from agentx import tradeoffs


def remedy(kind, value, confidence, eligibility="eligible", title=None):
    return {"kind": kind, "title": title or kind, "expected_value_minor": value,
            "confidence": confidence, "eligibility": eligibility,
            "because": "test"}


# merchant_refund is low risk, statutory_compensation medium, payment_dispute high.
LOW, MED, HIGH = "merchant_refund", "statutory_compensation", "payment_dispute"


# ─────────────────────────────────────────────────────────── dominance
def test_a_strictly_worse_remedy_is_dominated():
    better = remedy(LOW, 10_000, 0.9)
    worse = remedy(LOW, 5_000, 0.5)
    assert tradeoffs.dominates(better, worse)
    assert not tradeoffs.dominates(worse, better)


def test_identical_remedies_do_not_dominate_each_other():
    """Otherwise one of two equivalent routes vanishes for no reason."""
    a, b = remedy(LOW, 10_000, 0.7), remedy(LOW, 10_000, 0.7)
    assert not tradeoffs.dominates(a, b)
    assert not tradeoffs.dominates(b, a)


def test_a_genuine_tradeoff_is_not_dominance():
    """More money but riskier does not beat less money but safer."""
    rich = remedy(MED, 35_000, 0.7)
    safe = remedy(LOW, 9_250, 0.7)
    assert not tradeoffs.dominates(rich, safe)
    assert not tradeoffs.dominates(safe, rich)


# ─────────────────────────────────────────────────────────── frontier
def test_both_sides_of_a_real_tradeoff_survive():
    out = tradeoffs.analyse([remedy(MED, 35_000, 0.7), remedy(LOW, 9_250, 0.7)])
    assert len(out["frontier"]) == 2
    assert out["is_a_real_choice"] is True


def test_a_dominated_remedy_is_removed_and_explained():
    out = tradeoffs.analyse([remedy(LOW, 10_000, 0.9), remedy(LOW, 1_000, 0.2)])
    assert len(out["frontier"]) == 1
    assert len(out["dominated"]) == 1
    d = out["dominated"][0]
    assert d["beaten_by"] == LOW
    assert d["because"].strip(), "a dropped route must say why it was dropped"


def test_a_single_clear_winner_is_not_presented_as_a_choice():
    out = tradeoffs.analyse([remedy(LOW, 10_000, 0.9), remedy(LOW, 1_000, 0.2)])
    assert out["is_a_real_choice"] is False
    assert "no trade-off" in out["note"]


def test_blocked_remedies_are_never_offered():
    """A blocked remedy is not a worse choice — it is not a choice. Putting it on
    the frontier would imply the user could pick it."""
    out = tradeoffs.analyse([
        remedy(LOW, 10_000, 0.9),
        remedy(MED, 99_999, 0.99, eligibility="needs_evidence"),
        remedy(HIGH, 99_999, 0.99, eligibility="ineligible"),
    ])
    kinds = {f["kind"] for f in out["frontier"]}
    assert kinds == {LOW}
    assert out["considered"] == 1


def test_no_open_remedies_is_a_stated_outcome():
    out = tradeoffs.analyse([remedy(LOW, 1, 0.1, eligibility="needs_evidence")])
    assert out["frontier"] == []
    assert out["is_a_real_choice"] is False
    assert "nothing to choose" in out["note"]


def test_empty_input_does_not_raise():
    for value in ([], None):
        out = tradeoffs.analyse(value)
        assert out["frontier"] == [] and out["considered"] == 0


def test_frontier_order_is_stable():
    rows = [remedy(MED, 35_000, 0.7), remedy(LOW, 9_250, 0.7)]
    first = [f["kind"] for f in tradeoffs.analyse(rows)["frontier"]]
    for _ in range(3):
        assert [f["kind"] for f in tradeoffs.analyse(rows)["frontier"]] == first


def test_frontier_options_say_what_they_are_best_at():
    out = tradeoffs.analyse([remedy(MED, 35_000, 0.7), remedy(LOW, 9_250, 0.7)])
    by_kind = {f["kind"]: f for f in out["frontier"]}
    assert "most money back" in by_kind[MED]["best_at"]
    assert "least risk" in by_kind[LOW]["best_at"]


def test_missing_objective_values_are_treated_as_zero_not_crashes():
    out = tradeoffs.analyse([{"kind": LOW, "eligibility": "eligible"},
                             remedy(LOW, 10_000, 0.9)])
    assert out["considered"] == 2
    assert len(out["frontier"]) == 1


def test_an_unknown_remedy_kind_does_not_raise():
    out = tradeoffs.analyse([remedy("not_a_real_remedy", 100, 0.5)])
    assert len(out["frontier"]) == 1


# ─────────────────────────────────────────────────────────── on real cases
def test_a_real_case_exposes_a_real_tradeoff(tmp_path):
    """Scenario D is a flight delay: statutory compensation is worth four times
    the partial refund but carries more risk. Neither should dominate."""
    from agentx import demo, engine, store
    from agentx.execution import providers

    store.reset_for_tests(str(tmp_path / "tradeoff.db"))
    store.ensure_schema()
    providers.clear()
    providers.bootstrap()

    with store.connect() as conn:
        demo.reset(conn)
        result = demo.run(conn, "D", use_llm=False)
        snap = engine.snapshot(conn, result["case_id"])

    out = snap["tradeoffs"]
    assert out["is_a_real_choice"] is True, out["note"]
    assert len(out["frontier"]) >= 2


def test_tradeoffs_do_not_change_the_recommendation(tmp_path):
    """Additive only. `headline` and the ranked `remedies` must be untouched —
    the frontier is a second view, not a re-ranking."""
    from agentx import demo, eligibility, engine, store
    from agentx.execution import providers

    store.reset_for_tests(str(tmp_path / "tradeoff2.db"))
    store.ensure_schema()
    providers.clear()
    providers.bootstrap()

    with store.connect() as conn:
        demo.reset(conn)
        result = demo.run(conn, "D", use_llm=False)
        snap = engine.snapshot(conn, result["case_id"])
        rows = eligibility.load(conn, result["case_id"])
        best = eligibility.best(rows)

    assert [r["kind"] for r in snap["remedies"]] == [r["kind"] for r in rows]
    assert best is not None
    # The recommended remedy must itself be on the frontier — recommending a
    # strictly-dominated route would be incoherent.
    assert best["kind"] in {f["kind"] for f in snap["tradeoffs"]["frontier"]}
