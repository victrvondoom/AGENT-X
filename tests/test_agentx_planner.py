"""
The Resolution Planner — validation is the only vote that counts.

Every test builds a `Plan` by hand rather than through `compose()` where possible,
because the point is that `validate()` catches a bad plan REGARDLESS of who or
what produced it — a hand-built one, a composed one, or an LLM's proposal.
"""
from __future__ import annotations

from agentx.planner import Plan, Step, validate


def _plan(steps):
    return Plan(case_id="PX-TEST", strategy="merchant_refund", steps=steps)


def test_valid_plan_passes():
    p = _plan([
        Step(key="draft", action="draft", title="Draft"),
        Step(key="act", action="request_refund", title="Ask",
             capability="refund_request", prerequisites=["draft"],
             required_level=2, risk="medium",
             expected={"outcome": "accepted"}),
        Step(key="verify", action="verify", title="Verify",
             capability="outcome_verification", prerequisites=["act"],
             required_level=1, expected={"outcome": "done"}),
    ])
    v = validate(p)
    assert v["ok"], v["errors"]


def test_unknown_action_verb_fails():
    p = _plan([Step(key="a", action="teleport", title="x")])
    v = validate(p)
    assert not v["ok"]
    assert any("unknown action" in e for e in v["errors"])


def test_missing_prerequisite_fails():
    p = _plan([Step(key="a", action="draft", title="x",
                    prerequisites=["does_not_exist"])])
    v = validate(p)
    assert not v["ok"]
    assert any("does_not_exist" in e for e in v["errors"])


def test_branch_target_must_exist():
    p = _plan([Step(key="a", action="draft", title="x", on_success="nowhere")])
    v = validate(p)
    assert not v["ok"]
    assert any("on_success" in e for e in v["errors"])


def test_cycle_is_rejected():
    p = _plan([
        Step(key="a", action="draft", title="x", prerequisites=["b"]),
        Step(key="b", action="draft", title="y", prerequisites=["a"]),
    ])
    v = validate(p)
    assert not v["ok"]
    assert any("cycle" in e for e in v["errors"])


def test_prerequisite_must_come_earlier_in_the_plan():
    p = _plan([
        Step(key="a", action="draft", title="x", prerequisites=["b"]),
        Step(key="b", action="draft", title="y"),
    ])
    v = validate(p)
    assert not v["ok"]
    assert any("comes after" in e for e in v["errors"])


def test_high_risk_step_below_level_3_is_rejected():
    p = _plan([
        Step(key="a", action="draft", title="prep"),
        Step(key="esc", action="escalate", title="escalate", capability="escalation",
             prerequisites=["a"], required_level=1, risk="high"),
    ])
    v = validate(p)
    assert not v["ok"]
    assert any("high-risk" in e for e in v["errors"])


def test_irreversible_action_below_level_2_is_rejected():
    p = _plan([
        Step(key="a", action="draft", title="prep"),
        Step(key="cancel", action="cancel", title="cancel", capability="cancellation",
             prerequisites=["a"], required_level=1, risk="medium"),
    ])
    v = validate(p)
    assert not v["ok"]


def test_capability_with_no_provider_fails(monkeypatch):
    from agentx import capabilities as caps
    monkeypatch.setattr(caps, "available",
                        lambda cap_id, provider_hint=None: {"available": False, "reason": "no provider"})
    p = _plan([
        Step(key="a", action="request_refund", title="x", capability="refund_request",
             required_level=2, risk="medium"),
    ])
    v = validate(p)
    assert not v["ok"]
    assert any("no provider" in e for e in v["errors"])


def test_external_action_without_verification_fails():
    p = _plan([
        Step(key="a", action="request_refund", title="x", capability="refund_request",
             required_level=2, risk="medium", expected={"outcome": "accepted"}),
    ])
    v = validate(p)
    assert not v["ok"]
    assert any("never verifies" in e for e in v["errors"])


def test_escalation_without_a_prior_attempt_fails():
    p = _plan([
        Step(key="esc", action="escalate", title="escalate", capability="escalation",
             required_level=3, risk="high", expected={"outcome": "accepted"}),
        Step(key="verify", action="verify", title="verify",
             capability="outcome_verification", prerequisites=["esc"],
             required_level=1, expected={"outcome": "done"}),
    ])
    v = validate(p)
    assert not v["ok"]
    assert any("without a prior attempt" in e for e in v["errors"])


def test_duplicate_step_keys_fail():
    p = _plan([
        Step(key="a", action="draft", title="x"),
        Step(key="a", action="draft", title="y"),
    ])
    v = validate(p)
    assert not v["ok"]
    assert any("duplicate" in e for e in v["errors"])


def test_next_step_treats_failed_as_satisfied_for_its_failure_branch():
    from agentx.planner import next_step
    p = _plan([
        Step(key="a", action="request_refund", title="x", capability="refund_request",
             required_level=2, risk="medium", status="FAILED",
             on_failure="esc"),
        Step(key="esc", action="escalate", title="escalate", capability="escalation",
             prerequisites=["a"], required_level=3, risk="high"),
    ])
    nxt = next_step(p)
    assert nxt is not None
    assert nxt.key == "esc"
