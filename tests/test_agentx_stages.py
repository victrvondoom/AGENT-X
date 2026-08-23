"""
Stage tracks and proactive attention.

Two properties carry the weight:

  * **Every case state has a track.** A state with no guidance is a case the user
    is told nothing about, and states are added over time — so coverage is
    asserted against the state machine rather than against a list kept in step by
    hand.
  * **Alerts are only ever true.** The pattern this came from shipped a table of
    hardcoded weather warnings keyed by district. The pattern is worth having;
    canned alerts are not. Every alert here must trace to a row.
"""
from __future__ import annotations

import pytest

from agentx import stages
from agentx.case import STATE_COPY, TRANSITIONS
from agentx.ontology import TERMINAL_STATES


# ─────────────────────────────────────────────────────────── coverage
def test_every_case_state_has_a_track():
    missing = set(STATE_COPY) - set(stages.TRACKS)
    assert not missing, f"states with no guidance: {missing}"


def test_no_track_describes_a_state_that_does_not_exist():
    assert not set(stages.TRACKS) - set(STATE_COPY)


def test_every_track_says_what_agent_x_is_doing():
    for track in stages.TRACKS.values():
        assert track.doing.strip(), f"{track.state} has no 'doing'"


def test_next_states_are_derived_from_the_state_machine():
    """Restating transitions would let a track describe an old state machine."""
    for state, track in stages.TRACKS.items():
        expected = [s for s in TRANSITIONS.get(state, ()) if s != "WITHDRAWN"]
        assert track.as_dict()["next_states"] == expected


def test_terminal_states_are_marked_and_offer_no_next_step():
    for state in TERMINAL_STATES:
        d = stages.track(state)
        assert d and d["terminal"] is True


def test_every_goal_a_track_recommends_actually_exists():
    """The drift class that made four consumer buttons dead — asserted here too,
    because a track can name a goal just as an HTML file could."""
    from core import modes
    for track in stages.TRACKS.values():
        for goal in track.goals:
            assert goal in modes.CATALOGUE, (
                f"{track.state} recommends unknown goal {goal!r}")


def test_waiting_on_is_one_of_the_declared_values():
    for track in stages.TRACKS.values():
        assert track.waiting_on in ("agent", "you", "them", "nobody")


def test_states_that_wait_on_the_user_tell_them_what_to_do():
    for track in stages.TRACKS.values():
        if track.waiting_on == "you":
            assert track.you_can, f"{track.state} waits on the user but suggests nothing"


def test_unknown_state_has_no_track():
    assert stages.track("NOT_A_STATE") is None
    assert stages.track(None) is None


def test_catalogue_covers_every_track():
    assert {t["state"] for t in stages.catalogue()} == set(stages.TRACKS)


# ─────────────────────────────────────────────────────────── attention
def _snap(state="WAITING_EXTERNAL", **extra):
    base = {"case": {"state": state}, "deadlines": [], "questions": [],
            "approvals": [], "contradictions": []}
    base.update(extra)
    return base


def test_a_resolved_case_never_nags():
    for state in TERMINAL_STATES:
        assert stages.attention(_snap(state, questions=[{"question": "q"}],
                                      approvals=[{"id": "a"}])) == []


def test_nothing_pending_produces_no_alerts():
    assert stages.attention(_snap()) == []
    assert stages.briefing(_snap())["needs_you"] is False


def test_an_overdue_deadline_is_urgent():
    alerts = stages.attention(_snap(deadlines=[
        {"label": "Merchant reply", "days_left": -4.0, "overdue": True}]))
    assert alerts and alerts[0]["severity"] == stages.URGENT
    assert alerts[0]["kind"] == "deadline_passed"


def test_a_near_deadline_is_urgent_and_a_far_one_is_not():
    near = stages.attention(_snap(deadlines=[{"label": "x", "days_left": 2.0}]))
    far = stages.attention(_snap(deadlines=[{"label": "x", "days_left": 8.0}]))
    assert near[0]["severity"] == stages.URGENT
    assert far[0]["severity"] == stages.SOON


def test_a_distant_deadline_is_not_mentioned_at_all():
    assert stages.attention(_snap(deadlines=[{"label": "x", "days_left": 90.0}])) == []


def test_a_met_deadline_is_not_chased():
    assert stages.attention(_snap(deadlines=[
        {"label": "x", "days_left": -9.0, "overdue": True, "status": "met"}])) == []


def test_deadline_time_is_read_from_the_case_not_the_wall_clock():
    """Sandbox cases run on a movable clock. Computing days from `now()` would
    measure the clock's displacement, and every sandbox case would show its
    deadlines as long passed."""
    # Asserted behaviourally: the row's own figure is returned verbatim, and a
    # `due_at` that disagrees with it is ignored entirely.
    assert stages._days_left({"days_left": -3.0, "due_at": "2099-01-01T00:00:00Z"}) == -3.0
    assert stages._days_left({"days_left": 12.5}) == 12.5
    # No days_left means no opinion — never a wall-clock guess.
    assert stages._days_left({"due_at": "2020-01-01T00:00:00Z"}) is None


def test_pending_approvals_are_urgent():
    alerts = stages.attention(_snap("ACTION_REQUIRED", approvals=[{"id": "a"}]))
    assert any(a["kind"] == "approval_pending" and a["severity"] == stages.URGENT
               for a in alerts)


def test_an_open_question_is_urgent_when_the_case_is_blocked_on_it():
    blocked = stages.attention(_snap("NEEDS_INPUT", questions=[{"question": "q?"}]))
    waiting = stages.attention(_snap("WAITING_EXTERNAL", questions=[{"question": "q?"}]))
    assert blocked[0]["severity"] == stages.URGENT
    assert waiting[0]["severity"] == stages.SOON


def test_a_blocking_contradiction_is_surfaced():
    alerts = stages.attention(_snap(contradictions=[
        {"severity": "blocking", "predicate": "charge.amount"}]))
    assert any(a["kind"] == "contradiction" for a in alerts)


def test_alerts_are_ordered_most_urgent_first():
    alerts = stages.attention(_snap(
        "NEEDS_INPUT",
        deadlines=[{"label": "far", "days_left": 8.0},
                   {"label": "gone", "days_left": -2.0, "overdue": True}],
        questions=[{"question": "q?"}]))
    order = [stages._SEVERITY_ORDER[a["severity"]] for a in alerts]
    assert order == sorted(order)


def test_every_alert_names_what_it_is_about():
    alerts = stages.attention(_snap(
        "NEEDS_INPUT", deadlines=[{"label": "Merchant reply", "days_left": -1.0,
                                   "overdue": True}],
        questions=[{"question": "Which card?"}], approvals=[{"id": "a"}]))
    for alert in alerts:
        assert alert["message"].strip() and alert["severity"] in (
            stages.URGENT, stages.SOON, stages.INFO)


# ─────────────────────────────────────────────────────────── on real cases
def test_a_live_case_gets_a_briefing(tmp_path):
    from agentx import engine, store
    store.reset_for_tests(str(tmp_path / "stages.db"))
    store.ensure_schema()
    with store.connect() as conn:
        snap = engine.intake(
            conn, description="I was charged twice by Kartly for the same order, "
                              "45.00 GBP.", use_llm=False)
    brief = snap["briefing"]
    assert brief["track"]["state"] == snap["case"]["state"]
    assert brief["track"]["doing"]


def test_a_resolved_demo_case_needs_nothing(tmp_path):
    from agentx import demo, engine, store
    from agentx.execution import providers
    store.reset_for_tests(str(tmp_path / "stages2.db"))
    store.ensure_schema()
    providers.clear()
    providers.bootstrap()
    with store.connect() as conn:
        demo.reset(conn)
        result = demo.run(conn, "A", use_llm=False)
        snap = engine.snapshot(conn, result["case_id"])
    assert snap["case"]["state"] == "RESOLVED"
    assert snap["briefing"]["alerts"] == []
    assert snap["briefing"]["needs_you"] is False
