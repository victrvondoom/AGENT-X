"""
The Risk & Autonomy Governor — the boundary an agentic product lives or dies by.

Every test here is exhaustive rather than illustrative on purpose: `assess()` is
tested with no database, so a rule set this consequential can be verified without
standing up a case at all.
"""
from __future__ import annotations

from agentx import capabilities as caps
from agentx import governor


def test_irreversible_high_risk_always_requires_explicit_authorization():
    v = governor.assess(action="escalate", capability=caps.get("escalation"),
                        case_level=4, risk="high", confidence=0.95,
                        blocking_contradictions=0)
    assert v.requires_authorization
    assert v.rule == "irreversible_high_risk"


def test_blocking_contradiction_stops_the_action():
    v = governor.assess(action="request_refund", capability=caps.get("refund_request"),
                        case_level=4, risk="medium", confidence=0.95,
                        blocking_contradictions=1)
    assert not v.allow
    assert v.rule == "blocking_contradiction"


def test_reading_actions_proceed_despite_a_blocking_contradiction():
    v = governor.assess(action="inspect", capability=None, case_level=0,
                        risk="low", confidence=0.95, blocking_contradictions=3)
    assert v.allow


def test_absent_confidence_blocks_rather_than_assumes():
    v = governor.assess(action="request_refund", capability=caps.get("refund_request"),
                        case_level=4, risk="medium", confidence=None)
    assert not v.allow
    assert v.rule == "no_confidence_signal"


def test_confidence_below_floor_for_risk_class_blocks():
    v = governor.assess(action="escalate", capability=caps.get("escalation"),
                        case_level=4, risk="high", confidence=0.5)
    assert not v.allow
    assert v.rule == "below_confidence_floor"


def test_amount_above_ceiling_is_refused_not_truncated():
    v = governor.assess(action="request_refund", capability=caps.get("refund_request"),
                        case_level=3, risk="medium", confidence=0.9,
                        amount_minor=10_000_000, currency="INR")
    assert not v.allow
    assert v.rule == "above_amount_ceiling"


def test_level_2_prepares_but_does_not_send():
    v = governor.assess(action="request_refund", capability=caps.get("refund_request"),
                        case_level=2, risk="medium", confidence=0.9,
                        amount_minor=1000, currency="GBP")
    assert v.allow
    assert v.requires_authorization
    assert v.rule == "confirm_before_sending"


def test_level_3_allows_reversible_action_unattended():
    v = governor.assess(action="request_refund", capability=caps.get("refund_request"),
                        case_level=3, risk="medium", confidence=0.9,
                        amount_minor=1000, currency="GBP")
    assert v.allow
    assert not v.requires_authorization


def test_level_3_still_blocks_irreversible_action():
    v = governor.assess(action="cancel", capability=caps.get("cancellation"),
                        case_level=3, risk="medium", confidence=0.9)
    assert v.requires_authorization


def test_level_4_allows_irreversible_but_not_high_risk_unattended():
    v = governor.assess(action="cancel", capability=caps.get("cancellation"),
                        case_level=4, risk="medium", confidence=0.9)
    assert v.allow
    assert not v.requires_authorization


def test_verdict_carries_a_human_readable_prompt_when_authorization_needed():
    v = governor.assess(action="request_refund", capability=caps.get("refund_request"),
                        case_level=1, risk="medium", confidence=0.9,
                        amount_minor=100000, currency="GBP", counterparty="Kartly")
    assert v.requires_authorization
    assert "Kartly" in v.prompt
    assert "£" in v.prompt or "1,000" in v.prompt


def test_policy_snapshot_is_a_declared_artifact():
    snap = governor.policy_snapshot()
    assert "hard_rules" in snap
    assert len(snap["hard_rules"]) >= 4
    assert "confidence_floors" in snap
