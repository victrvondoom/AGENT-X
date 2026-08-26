"""
The follow-up agent — case-aware chasing on a clock, not a reminder that fires
regardless of what has happened since it was scheduled.
"""
from __future__ import annotations

import pytest

from agentx import case as case_mod
from agentx import chain, followup, ids, store
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
    c = case_mod.create(conn, description="test", autonomy_level=4)
    return case_mod.update(conn, c["id"], confidence=0.9)


class TestScopedFiring:
    def test_a_follow_up_does_not_fire_if_case_state_changed(self, conn, case):
        case_mod.transition(conn, case["id"], "INVESTIGATING", why="test")
        case_mod.transition(conn, case["id"], "ACTION_REQUIRED", why="test")
        case_mod.transition(conn, case["id"], "ACTION_SUBMITTED", why="test")
        case_mod.schedule_followup(conn, case["id"], kind="chase",
                                   due_at=ids.now(), require_state="WAITING_EXTERNAL")
        # Case is in ACTION_SUBMITTED, not WAITING_EXTERNAL — the follow-up
        # assumed a state the case never reached.
        due = followup.due(conn, as_of=ids.in_days(1))
        assert due == []

    def test_a_resolved_case_never_gets_woken(self, conn, case):
        case_mod.transition(conn, case["id"], "INVESTIGATING", why="t")
        case_mod.transition(conn, case["id"], "ACTION_REQUIRED", why="t")
        case_mod.transition(conn, case["id"], "ACTION_SUBMITTED", why="t")
        case_mod.transition(conn, case["id"], "RESOLVED", why="t")
        case_mod.schedule_followup(conn, case["id"], kind="chase", due_at=ids.now())
        due = followup.due(conn, as_of=ids.in_days(1))
        assert due == []

    def test_closing_a_case_cancels_its_outstanding_followups(self, conn, case):
        case_mod.transition(conn, case["id"], "INVESTIGATING", why="t")
        case_mod.transition(conn, case["id"], "ACTION_REQUIRED", why="t")
        case_mod.transition(conn, case["id"], "ACTION_SUBMITTED", why="t")
        case_mod.schedule_followup(conn, case["id"], kind="chase", due_at=ids.now())
        case_mod.transition(conn, case["id"], "RESOLVED", why="t")
        rows = case_mod.followups(conn, case["id"])
        assert all(r["status"] != "SCHEDULED" for r in rows)


class TestDeadlines:
    def test_only_statutory_and_scheme_deadlines_interrupt(self, conn, case):
        case_mod.add_deadline(conn, case["id"], kind="merchant_sla",
                              label="merchant SLA", due_at=ids.in_days(5))
        case_mod.add_deadline(conn, case["id"], kind="statutory",
                              label="statutory right", due_at=ids.in_days(5))
        out = followup._sweep_deadlines(conn, ids.in_days(2), "default")
        assert out["warned"] == 1  # only the statutory one

    def test_a_deadline_is_warned_once_not_every_sweep(self, conn, case):
        case_mod.add_deadline(conn, case["id"], kind="statutory",
                              label="right", due_at=ids.in_days(5))
        first = followup._sweep_deadlines(conn, ids.in_days(2), "default")
        second = followup._sweep_deadlines(conn, ids.in_days(2), "default")
        assert first["warned"] == 1
        assert second["warned"] == 0

    def test_missed_deadline_marked_and_recorded_on_chain(self, conn, case):
        case_mod.add_deadline(conn, case["id"], kind="statutory",
                              label="right", due_at=ids.in_days(1))
        followup._sweep_deadlines(conn, ids.in_days(5), "default")
        dls = case_mod.deadlines(conn, case["id"])
        assert dls[0]["status"] == "MISSED"
        rows = chain.readable(conn, case["id"])
        assert any(r["step"] == "deadline.missed" for r in rows)


class TestChaseAndEscalate:
    def test_chase_with_nothing_sent_cancels_itself(self, conn, case):
        case_mod.transition(conn, case["id"], "INVESTIGATING", why="t")
        case_mod.transition(conn, case["id"], "ACTION_REQUIRED", why="t")
        case_mod.transition(conn, case["id"], "ACTION_SUBMITTED", why="t")
        case_mod.transition(conn, case["id"], "WAITING_EXTERNAL", why="t")
        case_mod.schedule_followup(conn, case["id"], kind="chase", due_at=ids.now(),
                                   require_state="WAITING_EXTERNAL")
        out = followup.run_due(conn, as_of=ids.in_days(1))
        assert out[0]["action"] == "skipped"

    def test_low_autonomy_escalation_asks_rather_than_acts(self, conn):
        low = case_mod.create(conn, description="test", autonomy_level=2)
        low = case_mod.update(conn, low["id"], confidence=0.9)
        assert low is not None
        _walk_to_waiting(conn, low["id"])
        followup._plan_escalation(conn, low, ids.now(), why="test reason")
        c = case_mod.get(conn, low["id"])
        assert c is not None
        assert c["state"] == "ACTION_REQUIRED"
        # An authorization request was queued for escalate, not an actual escalation
        with conn.cursor() as cur:
            cur.execute("SELECT action FROM authorizations WHERE case_id = %s"
                        " AND granted IS NULL", (low["id"],))
            actions = [r[0] for r in cur.fetchall()]
        assert "escalate" in actions

    def test_high_autonomy_escalation_schedules_it_automatically(self, conn):
        high = case_mod.create(conn, description="test", autonomy_level=4)
        high = case_mod.update(conn, high["id"], confidence=0.9)
        assert high is not None
        _walk_to_waiting(conn, high["id"])
        followup._plan_escalation(conn, high, ids.now(), why="test reason")
        rows = case_mod.followups(conn, high["id"])
        assert any(r["kind"] == "escalate" and r["status"] == "SCHEDULED" for r in rows)


def _walk_to_waiting(conn, case_id: str) -> None:
    """Advance a fresh case through the states a real one passes before it can
    reach a follow-up decision at all — `_plan_escalation` assumes exactly this."""
    for state in ("INVESTIGATING", "ACTION_REQUIRED", "ACTION_SUBMITTED", "WAITING_EXTERNAL"):
        case_mod.transition(conn, case_id, state, why="test setup")
