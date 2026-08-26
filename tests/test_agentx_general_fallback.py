"""
The general-consumer-problem fallback — proof the engine does not just refuse a
narrative it cannot classify.

Before this existed, engine.understand() responded to "no hypothesis scored any
signal" by asking one clarifying question and parking the case in NEEDS_INPUT
forever; a genuinely novel problem had no path to a plan at all. That is the
"UNKNOWN CONSUMER PROBLEM" demo requirement failing by construction.

general_consumer_problem (agentx/ontology/definitions/general.yaml) is reached
only by engine.py naming it explicitly — it carries no phrases/patterns, so it
is mathematically excluded from understanding.hypotheses()'s normal scoring and
can never compete with, or dilute, a real classification.
"""
from __future__ import annotations

import pytest

from agentx import case as case_mod, engine, store
from agentx.execution import providers
from agentx.ontology import get as get_definition

UNCLASSIFIABLE = ("My smart fridge keeps texting my ex-landlord for no reason "
                  "and I want it to stop.")


@pytest.fixture(autouse=True)
def sqlite_engine(tmp_path):
    store.reset_for_tests(str(tmp_path / "fallback.db"))
    providers.clear()
    providers.bootstrap()
    yield


@pytest.fixture
def conn():
    with store.connect() as c:
        yield c


def test_general_consumer_problem_is_a_real_catalogue_entry():
    d = get_definition("general_consumer_problem")
    assert d is not None
    assert d.domain == "general"
    assert not d.phrases and not d.patterns, (
        "a phrase/pattern here would let it compete in normal classification")


def test_unclassifiable_narrative_still_reaches_a_plan(conn):
    snap = engine.intake(conn, description=UNCLASSIFIABLE, use_llm=False)
    case = snap["case"]
    assert case["problem_type"] == "general_consumer_problem"
    assert case["state"] == "ACTION_REQUIRED", (
        f"expected a plan awaiting approval, got {case['state']}: {snap}")
    assert snap["plan"] is not None
    assert snap["plan"]["strategy"] == "explanation"
    assert snap["plan"]["validation"]["ok"] is True


def test_unclassifiable_narrative_still_asks_a_clarifying_question(conn):
    """Falling back does not mean giving up on a better answer — the question
    stays open so an answer can still upgrade the case to a real problem type."""
    snap = engine.intake(conn, description=UNCLASSIFIABLE, use_llm=False)
    questions = [q["question"] for q in snap.get("questions", [])]
    assert any("more about what went wrong" in q for q in questions)


def test_fallback_plan_never_claims_a_specific_amount(conn):
    """explanation is 'no money moves' — the plan must not invent a figure for a
    problem Agent X does not understand."""
    snap = engine.intake(conn, description=UNCLASSIFIABLE, use_llm=False)
    for step in snap["plan"]["steps"]:
        assert step["params"].get("amount_minor") is None


def test_a_classifiable_narrative_is_unaffected(conn):
    """The fallback must not shadow real classification for an ordinary case."""
    snap = engine.intake(
        conn, description="Kartly charged me twice for order 402-9938271, "
                          "2,399 INR each on 2026-08-02.", use_llm=False)
    assert snap["case"]["problem_type"] == "duplicate_charge"
