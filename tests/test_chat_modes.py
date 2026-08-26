"""
Answer modes — and the drift that made four of them do nothing.

The bug this file exists to prevent: the goal picker offered "Verify Booking",
"Hidden Fees", "Refund Policy" and "Report Issue", the UI sent those ids, and
`ask()` had no branch for any of them. Every one fell through to the default
prompt. The buttons worked; the features did not exist.

So the tests below are mostly about the CATALOGUE being the single source of
truth — that nothing can be offered without being routed, and nothing routed can
go unoffered.
"""
from __future__ import annotations

import pytest

from core import modes


# ─────────────────────────────────────────────────────── the catalogue
def test_every_offered_mode_actually_changes_the_prompt():
    """The original bug, asserted directly."""
    for mode in modes.CATALOGUE.values():
        assert mode.prompt.strip(), f"{mode.id} is offered but shapes nothing"


def _offered_goals(catalogue: dict) -> set[str]:
    """Ids the picker offers as GOALS — the input group is excluded.

    Dictation is listed in the picker but is not a goal: it changes how the user
    asks, not how Agent X answers, and it must never reach `ask()` as a
    capability.
    """
    return {m["id"] for g in catalogue["groups"] if g["id"] != modes.INPUT
            for m in g["modes"]}


def test_every_catalogued_mode_is_offered_in_a_group():
    offered = _offered_goals(modes.catalogue())
    assert offered == set(modes.CATALOGUE), (
        f"catalogue and picker disagree: {offered ^ set(modes.CATALOGUE)}")


def test_the_input_track_is_listed_but_is_not_a_routable_goal():
    group = next(g for g in modes.catalogue()["groups"] if g["id"] == modes.INPUT)
    assert group["modes"], "the input track should be offered"
    for entry in group["modes"]:
        assert modes.get(entry["id"]) is None, (
            f"{entry['id']} is an input track and must not route as a goal")
        assert entry.get("action"), "an input track must say what it does"


def test_the_previously_dead_consumer_modes_are_routed():
    for mode_id in ("verify_booking", "hidden_fees", "refund_policy", "report_issue"):
        mode = modes.get(mode_id)
        assert mode is not None, f"{mode_id} is offered by the UI but unroutable"
        assert mode.prompt.strip()


def test_unknown_mode_is_simply_no_mode():
    for value in ("auto", None, "", "not_a_mode"):
        assert modes.get(value) is None


def test_groups_are_the_three_declared_ones():
    ids = [g["id"] for g in modes.catalogue()["groups"]]
    assert ids == [modes.GENERAL, modes.CONSUMER, modes.INPUT]


def test_grounded_modes_are_marked_as_such():
    """The picker labels them, so the flag has to be true of exactly the modes
    that attach real retrieved material."""
    for mode in modes.CATALOGUE.values():
        assert mode.as_dict()["grounded"] == (mode.context is not None)
    grounded = {m.id for m in modes.CATALOGUE.values() if m.context}
    assert grounded == {"escalation_route", "what_its_worth",
                        "case_status", "needs_attention"}


# ─────────────────────────────────────────────────── grounded providers
def test_escalation_route_retrieves_real_guidance():
    ctx = modes._escalation_context(
        "my bank refuses to reverse an unauthorized card transaction", "default")
    assert ctx and "escalation guidance" in ctx.lower()


def test_escalation_route_returns_nothing_when_uncovered():
    """Silence beats naming a regulator from memory to someone about to spend a
    week writing to the wrong office."""
    assert modes._escalation_context("how do I bake sourdough bread", "default") is None


def test_escalation_prompt_forbids_naming_a_regulator_from_memory():
    mode = modes.get("escalation_route")
    assert mode is not None
    prompt = mode.prompt
    assert "ONLY" in prompt and "from memory" in prompt


def test_worth_mode_refuses_to_estimate_without_history(tmp_path, monkeypatch):
    from agentx import store as axstore
    axstore.reset_for_tests(str(tmp_path / "worth.db"))
    axstore.ensure_schema()
    ctx = modes._worth_context("is this worth pursuing", "default")
    assert ctx and "too few cases" in ctx
    assert "do not estimate" in ctx


def test_worth_mode_reports_real_history(tmp_path):
    from agentx import demo, store as axstore
    from agentx.execution import providers

    axstore.reset_for_tests(str(tmp_path / "worth2.db"))
    axstore.ensure_schema()
    providers.clear()
    providers.bootstrap()
    with axstore.connect() as conn:
        demo.reset(conn)
        for key in sorted(demo.SCENARIOS):
            demo.run(conn, key, use_llm=False)

    ctx = modes._worth_context("is this worth pursuing", "default")
    assert ctx and "Closed cases on record" in ctx
    assert "not an industry statistic" in ctx, (
        "history must be labelled as Agent X's own, not a benchmark")


def test_grounded_context_failure_never_breaks_an_answer(monkeypatch):
    """A grounded provider that raises must degrade to a normal answer."""
    def boom(*_a, **_k):
        raise RuntimeError("corpus unavailable")

    monkeypatch.setattr(modes, "_escalation_context", boom)
    bad = modes.Mode("x", "X", modes.CONSUMER, prompt=" p", context=boom)
    monkeypatch.setitem(modes.CATALOGUE, "x", bad)

    from core import ask as ask_mod
    monkeypatch.setattr(ask_mod.client, "embed", lambda *a, **k: [0.0] * 384)
    monkeypatch.setattr(ask_mod, "_retrieve", lambda *a, **k: ([], []))
    answer, sources = ask_mod.ask("anything", capability="x")
    assert "don't have anything on record" in answer


# ─────────────────────────────────────────────────── HTTP surface
def test_modes_endpoint_serves_the_catalogue():
    from fastapi.testclient import TestClient
    from app.main import app

    data = TestClient(app).get("/api/modes").json()
    assert _offered_goals(data) == set(modes.CATALOGUE)


def test_ask_accepts_every_offered_mode():
    """End to end: no offered id may 400, 422 or 500."""
    from fastapi.testclient import TestClient
    from app.main import app
    from core import ask as ask_mod

    client = TestClient(app)
    original = ask_mod.ask
    try:
        ask_mod.ask = lambda *a, **k: ("ok", [])
        import app.main as main_mod
        main_mod.ask_memory = lambda *a, **k: ("ok", [])
        for mode_id in modes.CATALOGUE:
            r = client.post("/api/ask", json={"query": "test", "capability": mode_id})
            assert r.status_code == 200, f"{mode_id} -> {r.status_code}"
            assert r.json()["capability"] == mode_id
    finally:
        ask_mod.ask = original
