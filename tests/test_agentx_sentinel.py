"""
The sentinel — self-healing that cannot heal its way around the governor.

A watchdog that repairs things on its own is the most dangerous component to add
to a system whose safety model is "consequential actions need approval", because
acting unasked is the entire point of it. So the tests that matter here are the
refusals:

  * no remediation escapes `governor.assess()` at any autonomy level;
  * a broken audit chain is never "repaired" — a watchdog that rewrites a chain
    is indistinguishable from one that forges it;
  * a dry run has no side effects;
  * a remediation is not believed because it returned, it is believed because
    re-reading the case shows the stall is gone.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agentx import case as case_mod, chain, engine, sentinel, store
from agentx.execution import providers


@pytest.fixture(autouse=True)
def sqlite_engine(tmp_path):
    store.reset_for_tests(str(tmp_path / "sentinel.db"))
    providers.clear()
    providers.bootstrap()
    yield


@pytest.fixture
def conn():
    with store.connect() as c:
        yield c


def later(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def open_case(conn, *, autonomy=2, confidence=None, state=None):
    snap = engine.intake(
        conn, description="I was charged twice by Kartly for order 402-9938271, "
                          "12.00 GBP each on 2026-07-11.",
        use_llm=False, autonomy_level=autonomy)
    case_id = snap["case"]["id"]
    if confidence is not None:
        case_mod.update(conn, case_id, confidence=confidence)
    if state:
        case_mod.transition(conn, case_id, state, why="test setup")
    return case_id


# ─────────────────────────────────────────────────────── detection
def test_a_fresh_case_is_not_stuck(conn):
    open_case(conn)
    assert sentinel.sweep(conn)["healthy"] is True


def test_a_long_silent_case_is_detected(conn):
    case_id = open_case(conn, state="INVESTIGATING")
    stalls = sentinel.inspect(conn, case_mod.get(conn, case_id), as_of=later(30))
    assert any(s.kind == "case_stalled" for s in stalls)


def test_a_case_is_not_stuck_just_below_the_threshold(conn):
    """The threshold is declared so it can be argued with; it must also hold."""
    case_id = open_case(conn, state="INVESTIGATING")
    days = int(sentinel.STALE_PLAN_DAYS) - 1
    stalls = sentinel.inspect(conn, case_mod.get(conn, case_id), as_of=later(days))
    assert not any(s.kind == "case_stalled" for s in stalls)


def test_a_closed_case_is_never_reported_as_stuck(conn):
    case_id = open_case(conn)
    case_mod.transition(conn, case_id, "WITHDRAWN", why="test")
    stalls = sentinel.inspect(conn, case_mod.get(conn, case_id), as_of=later(400))
    assert [s for s in stalls if s.kind != "chain_broken"] == []


def test_detection_is_deterministic(conn):
    case_id = open_case(conn, state="INVESTIGATING")
    case = case_mod.get(conn, case_id)
    first = [s.kind for s in sentinel.inspect(conn, case, as_of=later(30))]
    for _ in range(3):
        assert [s.kind for s in sentinel.inspect(conn, case, as_of=later(30))] == first


def test_every_stall_names_a_declared_remediation(conn):
    case_id = open_case(conn, state="INVESTIGATING")
    for stall in sentinel.inspect(conn, case_mod.get(conn, case_id), as_of=later(30)):
        assert stall.remediation in sentinel.REMEDIATIONS
        assert stall.severity in sentinel._SEVERITY_ORDER
        assert stall.detail.strip()


# ─────────────────────────────────────────────────── integrity
def test_a_tampered_chain_is_critical(conn):
    case_id = open_case(conn)
    with conn.cursor() as cur:
        cur.execute("UPDATE case_chain SET detail = %s WHERE case_id = %s AND seq = 1",
                    ('{"tampered":true}', case_id))
    stalls = sentinel.inspect(conn, case_mod.get(conn, case_id))
    broken = [s for s in stalls if s.kind == "chain_broken"]
    assert broken and broken[0].severity == sentinel.CRITICAL


def test_a_broken_chain_is_never_auto_repaired(conn):
    """The one thing self-healing must not touch. A watchdog that rewrites an
    audit chain to make it verify has forged it."""
    case_id = open_case(conn)
    with conn.cursor() as cur:
        cur.execute("UPDATE case_chain SET detail = %s WHERE case_id = %s AND seq = 1",
                    ('{"tampered":true}', case_id))
    broken = next(s for s in sentinel.inspect(conn, case_mod.get(conn, case_id))
                  if s.kind == "chain_broken")
    remedy = sentinel.heal(conn, broken, apply=True)
    assert remedy.allowed is False
    assert remedy.status == sentinel.NEEDS_HUMAN
    # And it is still broken — nothing tried to "fix" it.
    assert chain.verify(conn, case_id)["ok"] is False


# ─────────────────────────────────────────────────── the governor
def test_no_remediation_escapes_the_governor_at_any_level(conn):
    """The invariant the module promises. Swept, not spot-checked."""
    escapes = []
    for level in range(0, 5):
        for confidence in (0.0, 0.3, 0.5, 0.9, 1.0):
            case_id = open_case(conn, autonomy=level, confidence=confidence,
                                state="INVESTIGATING")
            case = case_mod.get(conn, case_id)
            for stall in sentinel.inspect(conn, case, as_of=later(30)):
                remedy = sentinel.assess(conn, stall, case)
                spec = sentinel.REMEDIATIONS[stall.remediation]
                if remedy.allowed and spec["action"] == "none":
                    escapes.append((level, confidence, stall.remediation))
    assert not escapes, f"self-healing bypassed the governor: {escapes}"


def test_escalation_is_not_a_remediation_the_sentinel_can_perform():
    """A watchdog must not decide to involve a regulator because a step looked
    slow. Asserted against the whole vocabulary, not one detector."""
    for name, spec in sentinel.REMEDIATIONS.items():
        assert spec["action"] != "escalate", (
            f"{name} would let the sentinel escalate autonomously")


def test_flag_for_human_is_never_executed(conn):
    case_id = open_case(conn, autonomy=4, confidence=1.0)
    stall = sentinel.Stall(kind="x", severity=sentinel.LOW, case_id=case_id,
                           workspace="default", detail="d",
                           remediation="flag_for_human")
    remedy = sentinel.heal(conn, stall, apply=True)
    assert remedy.allowed is False and remedy.status == sentinel.NEEDS_HUMAN


def test_an_action_needing_approval_is_reported_not_performed(conn):
    """And no approval is requested on the user's behalf either — a watchdog
    manufacturing approval requests trains people to approve things they did not
    start."""
    case_id = open_case(conn, autonomy=1, confidence=0.2, state="INVESTIGATING")
    case = case_mod.get(conn, case_id)
    stalls = sentinel.inspect(conn, case, as_of=later(30))
    assert stalls
    before = len(engine.pending_approvals(conn, case_id))
    remedy = sentinel.heal(conn, stalls[0], apply=True, as_of=later(30))
    assert remedy.status in (sentinel.NEEDS_HUMAN, sentinel.UNRESOLVED)
    assert len(engine.pending_approvals(conn, case_id)) == before


# ─────────────────────────────────────────────────── healing
def test_a_dry_run_changes_nothing(conn):
    case_id = open_case(conn, autonomy=4, confidence=0.92, state="INVESTIGATING")
    case = case_mod.get(conn, case_id)
    stall = sentinel.inspect(conn, case, as_of=later(30))[0]
    before_state = case["state"]
    before_chain = chain.head(conn, case_id)

    remedy = sentinel.heal(conn, stall, apply=False, as_of=later(30))
    assert remedy.verified == "not_attempted"
    assert case_mod.get(conn, case_id)["state"] == before_state
    assert chain.head(conn, case_id) == before_chain


def test_a_permitted_heal_runs_and_is_verified(conn):
    case_id = open_case(conn, autonomy=4, confidence=0.92, state="INVESTIGATING")
    stall = sentinel.inspect(conn, case_mod.get(conn, case_id), as_of=later(30))[0]
    remedy = sentinel.heal(conn, stall, apply=True, as_of=later(30))
    assert remedy.allowed is True
    assert remedy.status == sentinel.HEALED
    assert remedy.verified == "healed"


def test_healing_is_recorded_on_the_chain(conn):
    case_id = open_case(conn, autonomy=4, confidence=0.92, state="INVESTIGATING")
    stall = sentinel.inspect(conn, case_mod.get(conn, case_id), as_of=later(30))[0]
    sentinel.heal(conn, stall, apply=True, as_of=later(30))
    steps = [r["step"] for r in chain.rows(conn, case_id)]
    assert "sentinel.healing" in steps
    assert "sentinel.verified" in steps


def test_healing_leaves_the_chain_intact(conn):
    case_id = open_case(conn, autonomy=4, confidence=0.92, state="INVESTIGATING")
    stall = sentinel.inspect(conn, case_mod.get(conn, case_id), as_of=later(30))[0]
    sentinel.heal(conn, stall, apply=True, as_of=later(30))
    assert chain.verify(conn, case_id)["ok"] is True


def test_verification_re_reads_rather_than_trusting_the_call(conn, monkeypatch):
    """A remediation that runs without moving the case must report still_stuck."""
    case_id = open_case(conn, autonomy=4, confidence=0.92, state="INVESTIGATING")
    stall = sentinel.inspect(conn, case_mod.get(conn, case_id), as_of=later(30))[0]
    monkeypatch.setattr(sentinel, "_perform", lambda *a, **k: None)
    remedy = sentinel.heal(conn, stall, apply=True, as_of=later(30))
    assert remedy.verified == "still_stuck"
    assert remedy.status == sentinel.UNRESOLVED


def test_a_failing_remediation_does_not_raise(conn, monkeypatch):
    """A watchdog that crashes on a failed repair stops watching everything else."""
    case_id = open_case(conn, autonomy=4, confidence=0.92, state="INVESTIGATING")
    stall = sentinel.inspect(conn, case_mod.get(conn, case_id), as_of=later(30))[0]

    def boom(*_a, **_k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(sentinel, "_perform", boom)
    remedy = sentinel.heal(conn, stall, apply=True, as_of=later(30))
    assert remedy.status == sentinel.UNRESOLVED
    assert "provider down" in remedy.detail
    # The failure is on the record, not swallowed.
    assert "sentinel.failed" in [r["step"] for r in chain.rows(conn, case_id)]


def test_healing_a_vanished_case_is_handled(conn):
    stall = sentinel.Stall(kind="case_stalled", severity=sentinel.HIGH,
                           case_id="PX-00000", workspace="default", detail="d",
                           remediation="run_followup")
    remedy = sentinel.heal(conn, stall, apply=True)
    assert remedy.status == sentinel.UNRESOLVED


# ─────────────────────────────────────────────────── sweep + HTTP
def test_sweep_reports_healthy_when_nothing_is_stuck(conn):
    open_case(conn)
    out = sentinel.sweep(conn)
    assert out["healthy"] is True and out["stalls"] == []


def test_sweep_defaults_to_not_applying(conn):
    open_case(conn, state="INVESTIGATING")
    out = sentinel.sweep(conn, as_of=later(30))
    assert out["applied"] is False
    assert all(r["verified"] == "not_attempted" for r in out["remedies"])


def test_sentinel_endpoint_is_read_only():
    from fastapi.testclient import TestClient
    from app.main import app

    data = TestClient(app).get("/api/agentx/sentinel").json()
    assert data["applied"] is False
    assert "stalls" in data and "remedies" in data


def test_health_declares_self_healing_is_governed():
    from fastapi.testclient import TestClient
    from app.main import app

    healing = TestClient(app).get("/api/agentx/health").json()["self_healing"]
    assert healing["governed"] is True
