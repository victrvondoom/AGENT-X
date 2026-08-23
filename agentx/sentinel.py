"""
The sentinel — Agent X watching its own work for the cases that got stuck.

`followup.py` is the clock: it fires the chases and escalations that are DUE. It
is good at what it does and this module does not duplicate it. The sentinel
answers the question the clock cannot, because the clock only ever looks at rows
that scheduled themselves:

    what is stuck for a reason nothing scheduled?

An execution that failed and was never retried. A plan whose current step has not
moved in a fortnight. An approval nobody granted, quietly rotting while the user
believes Agent X is working. A hash chain that no longer verifies. None of those
raise an error at the time. They are silences, and a product that promises to
chase a company until it pays cannot itself go quiet.

THE LOOP, AND WHAT IS DIFFERENT ABOUT AGENT X'S VERSION

    detect → diagnose → remediate → VERIFY → record

Detection is DETERMINISTIC. Not a model reading logs and forming an opinion —
every finding below is a query over real rows with a stated threshold, so a stall
either exists or does not and the answer is the same on every run.

Remediation is GOVERNED. This is the part that matters most and it is where a
self-healing system is most dangerous: the whole point of automatic repair is
acting without being asked, and Agent X's entire safety model is that
consequential actions require authorisation. So the sentinel proposes through
`governor.assess()` exactly like every other actor, and an action the governor
gates stays gated. A watchdog that could escalate a dispute to a regulator on its
own, because a step looked stuck, would be a hole straight through the approval
model — so `heal()` cannot open one, and `tests/test_agentx_sentinel.py` sweeps
every remediation against every autonomy level to prove it.

Verification RE-READS. A remediation is not believed because it returned success;
the case is re-examined afterwards and the stall must actually be gone. That is
the same discipline `runner.verify()` applies to a merchant's reply, for the same
reason.

NOTHING HERE IS AI. The source this pattern came from used one model to spot
anomalies in logs and another to recommend fixes. Agent X has the advantage that
its subject is its own database rather than unstructured logs, so the model is
unnecessary — and a deterministic watchdog can be reasoned about, which one built
on a language model's opinion of a log file cannot.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

from agentx import capabilities as caps
from agentx import case as case_mod
from agentx import chain, governor, ids, store
from agentx.execution import runner
from agentx.ontology import TERMINAL_STATES

CRITICAL, HIGH, MEDIUM, LOW = "critical", "high", "medium", "low"
_SEVERITY_ORDER = {CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3}

# Status of one detected stall, mirroring the incident lifecycle.
DETECTED, HEALING, HEALED, UNRESOLVED, NEEDS_HUMAN = (
    "detected", "healing", "healed", "unresolved", "needs_human")

# How long a thing may sit before it counts as stuck. Declared here rather than
# buried in a branch so they can be argued with. Measured on the CASE's clock
# (`days_left`, `updated_at` deltas), never wall-clock — sandbox cases run on a
# movable clock and would otherwise all look abandoned.
STALE_APPROVAL_DAYS = 3.0
STALE_QUESTION_DAYS = 7.0
STALE_PLAN_DAYS = 14.0

# Remediations the sentinel is allowed to propose at all. A verb absent here
# cannot be reached by self-healing however a detector is written.
REMEDIATIONS = {
    "retry_execution": {"action": "send", "capability": "email_interaction",
                        "risk": "medium",
                        "why": "the previous attempt failed and nothing retried it"},
    "run_followup": {"action": "send", "capability": "email_interaction",
                     "risk": "low",
                     "why": "a scheduled follow-up is overdue and did not fire"},
    "reopen_investigation": {"action": "analyse",
                             "capability": "eligibility_determination",
                             "risk": "low",
                             "why": "the case stalled before it reached a plan"},
    # Deliberately NOT a remediation the sentinel performs: escalation. It is
    # consequential and irreversible-ish, and a watchdog deciding to involve a
    # regulator because a step looked slow is exactly the failure this design
    # refuses. A stalled case that needs escalating is raised to a human.
    "flag_for_human": {"action": "none", "capability": None, "risk": "low",
                       "why": "this needs a person to look at it"},
}


@dataclass
class Stall:
    """One thing that is stuck, and why."""
    kind: str
    severity: str
    case_id: str
    workspace: str
    detail: str
    remediation: str = "flag_for_human"
    evidence: dict = field(default_factory=dict)
    status: str = DETECTED

    def as_dict(self) -> dict:
        d = asdict(self)
        d["remediation_why"] = REMEDIATIONS.get(self.remediation, {}).get("why", "")
        return d


# ─────────────────────────────────────────────────────────────────────────────
# detection — every finding is a query over real rows
# ─────────────────────────────────────────────────────────────────────────────
def _age_days(then: str | None, now: str) -> float | None:
    """Days between two ISO-8601 UTC stamps, or None if either is unusable."""
    if not then:
        return None
    from datetime import datetime
    try:
        a = datetime.fromisoformat(str(then).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(now).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return round((b - a).total_seconds() / 86400.0, 2)


def _chain_intact(conn, case_id: str) -> tuple[bool, str]:
    try:
        result = chain.verify(conn, case_id)
    except Exception as exc:
        return False, f"the chain could not be verified: {exc}"
    return bool(result.get("ok")), str(result.get("reason") or "")


def inspect(conn, case: dict, *, as_of: str | None = None) -> list[Stall]:
    """Every stall on one case. Empty is the normal, healthy answer."""
    case_id, workspace = case["id"], case.get("workspace", "default")
    state = (case.get("state") or "").upper()
    now = as_of or ids.now()
    out: list[Stall] = []

    # ── integrity first ───────────────────────────────────────────────────
    # Agent X's equivalent of "is the container healthy" is "does the record
    # still verify". Nothing else on the case matters if this fails, and it is
    # the one stall that must never be auto-remediated — a watchdog that
    # "repairs" a broken audit chain is indistinguishable from one that forges
    # it, so this is always raised to a human.
    intact, reason = _chain_intact(conn, case_id)
    if not intact:
        out.append(Stall(kind="chain_broken", severity=CRITICAL, case_id=case_id,
                         workspace=workspace, remediation="flag_for_human",
                         detail=f"This case's audit chain does not verify: {reason}",
                         evidence={"reason": reason}))

    if state in TERMINAL_STATES:
        # A closed case cannot be stuck. Integrity is still checked above,
        # because a closed case's record can still be corrupted.
        return out

    # ── a failed execution nobody retried ─────────────────────────────────
    executions = runner.history(conn, case_id)
    for index, execution in enumerate(executions):
        if (execution.get("state") or "").upper() not in ("FAILED", "ERROR"):
            continue
        later = executions[index + 1:] if index + 1 < len(executions) else []
        retried = any((e.get("action") == execution.get("action")
                       and (e.get("state") or "").upper() not in ("FAILED", "ERROR"))
                      for e in later)
        if not retried:
            out.append(Stall(
                kind="execution_failed", severity=HIGH, case_id=case_id,
                workspace=workspace, remediation="retry_execution",
                detail=f"{execution.get('action') or 'An action'} failed and was "
                       f"never retried: {execution.get('error') or 'no reason recorded'}",
                evidence={"execution_id": execution.get("id"),
                          "action": execution.get("action")}))

    # ── an approval rotting ───────────────────────────────────────────────
    if state == "ACTION_REQUIRED":
        age = _age_days(case.get("updated_at"), now)
        if age is not None and age >= STALE_APPROVAL_DAYS:
            out.append(Stall(
                kind="approval_stale", severity=MEDIUM, case_id=case_id,
                workspace=workspace, remediation="flag_for_human",
                detail=f"An action has been waiting {age:.0f} days for approval. "
                       f"Nothing will happen until someone decides.",
                evidence={"days": age}))

    # ── a question nobody answered ────────────────────────────────────────
    if state == "NEEDS_INPUT":
        age = _age_days(case.get("updated_at"), now)
        if age is not None and age >= STALE_QUESTION_DAYS:
            out.append(Stall(
                kind="question_stale", severity=LOW, case_id=case_id,
                workspace=workspace, remediation="flag_for_human",
                detail=f"This case has been waiting {age:.0f} days for an answer "
                       f"and cannot move without one.",
                evidence={"days": age}))

    # ── a plan that stopped moving ────────────────────────────────────────
    age = _age_days(case.get("updated_at"), now)
    if (state not in ("ACTION_REQUIRED", "NEEDS_INPUT")
            and age is not None and age >= STALE_PLAN_DAYS):
        out.append(Stall(
            kind="case_stalled", severity=HIGH, case_id=case_id,
            workspace=workspace,
            remediation=("reopen_investigation" if state == "INVESTIGATING"
                         else "run_followup"),
            detail=f"Nothing has happened on this case for {age:.0f} days while it "
                   f"sits at {state.lower().replace('_', ' ')}.",
            evidence={"days": age, "state": state}))

    out.sort(key=lambda s: _SEVERITY_ORDER.get(s.severity, 9))
    return out


def scan(conn, *, workspace: str = "default", limit: int = 100,
         as_of: str | None = None) -> list[Stall]:
    """Every stall across a workspace's cases, most severe first."""
    store.ensure_schema()
    out: list[Stall] = []
    for row in case_mod.list_cases(conn, workspace=workspace, limit=limit):
        case = case_mod.get(conn, row["id"])
        if case:
            out.extend(inspect(conn, case, as_of=as_of))
    out.sort(key=lambda s: _SEVERITY_ORDER.get(s.severity, 9))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# remediation — proposed through the governor, never around it
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Remedy:
    """What the sentinel would do, whether it may, and what happened."""
    stall: str
    case_id: str
    remediation: str
    allowed: bool
    requires_authorization: bool
    status: str
    detail: str
    verified: str = "not_attempted"     # healed | still_stuck | not_attempted

    def as_dict(self) -> dict:
        return asdict(self)


def assess(conn, stall: Stall, case: dict) -> Remedy:
    """Would this remediation be permitted? Decided by the governor, not here."""
    spec = REMEDIATIONS.get(stall.remediation)
    if spec is None or spec["action"] == "none":
        return Remedy(stall=stall.kind, case_id=stall.case_id,
                      remediation=stall.remediation, allowed=False,
                      requires_authorization=False, status=NEEDS_HUMAN,
                      detail="This needs a person; the sentinel will not act on it.")

    capability = caps.get(spec["capability"]) if spec.get("capability") else None
    if capability is None:
        return Remedy(stall=stall.kind, case_id=stall.case_id,
                      remediation=stall.remediation, allowed=False,
                      requires_authorization=False, status=NEEDS_HUMAN,
                      detail="no capability backs this remediation; a person "
                             "must handle it.")

    verdict = governor.assess(
        action=spec["action"],
        capability=capability,
        case_level=int(case.get("autonomy_level") or 0),
        risk=spec["risk"],
        confidence=float(case.get("confidence") or 0.0),
    )
    return Remedy(
        stall=stall.kind, case_id=stall.case_id, remediation=stall.remediation,
        allowed=bool(verdict.allow),
        requires_authorization=bool(verdict.requires_authorization),
        status=DETECTED,
        detail=(verdict.explain or
                ("permitted at this autonomy level" if verdict.allow
                 else "not permitted at this autonomy level")))


def heal(conn, stall: Stall, *, apply: bool = False,
         as_of: str | None = None) -> Remedy:
    """Attempt one remediation. Records what it did, and re-reads to confirm.

    `apply=False` is the default and returns the proposal without touching
    anything — a watchdog whose dry run has side effects is not a dry run.
    """
    case = case_mod.get(conn, stall.case_id)
    if not case:
        return Remedy(stall=stall.kind, case_id=stall.case_id,
                      remediation=stall.remediation, allowed=False,
                      requires_authorization=False, status=UNRESOLVED,
                      detail="the case no longer exists")

    remedy = assess(conn, stall, case)
    if not apply or not remedy.allowed or remedy.requires_authorization:
        # An action needing authorisation is NOT performed here and no
        # authorisation is requested on the user's behalf either: the sentinel's
        # job is to notice, and manufacturing an approval request from a
        # watchdog would train users to approve things they did not initiate.
        if remedy.requires_authorization:
            remedy.status = NEEDS_HUMAN
        return remedy

    before = (case.get("state"), case.get("updated_at"))
    remedy.status = HEALING
    chain.append(conn, stall.case_id, "sentinel.healing", "AGENT",
                 {"stall": stall.kind, "remediation": stall.remediation,
                  "severity": stall.severity, "because": stall.detail})

    try:
        _perform(conn, stall, case, as_of=as_of)
    except Exception as exc:
        remedy.status = UNRESOLVED
        remedy.detail = f"the remediation failed: {exc}"
        chain.append(conn, stall.case_id, "sentinel.failed", "AGENT",
                     {"stall": stall.kind, "because": str(exc)})
        return remedy

    # Re-read rather than believe the call. `runner.verify()` treats a
    # counterparty's reply the same way, for the same reason.
    after = case_mod.get(conn, stall.case_id) or {}
    still = [s for s in inspect(conn, after, as_of=as_of) if s.kind == stall.kind]
    remedy.verified = "still_stuck" if still else "healed"
    remedy.status = UNRESOLVED if still else HEALED
    remedy.detail = ("the case moved and the stall is gone" if not still
                     else "the remediation ran but the case is still stuck")
    chain.append(conn, stall.case_id, "sentinel.verified", "AGENT",
                 {"stall": stall.kind, "verified": remedy.verified,
                  "state_before": before[0], "state_after": after.get("state")})
    return remedy


def _perform(conn, stall: Stall, case: dict, *, as_of: str | None = None) -> None:
    """Carry out a permitted remediation using the engine's own entry points."""
    if stall.remediation == "run_followup":
        from agentx import followup
        followup.run_due(conn, as_of=as_of, limit=5)
    elif stall.remediation == "reopen_investigation":
        from agentx import engine
        engine.investigate(conn, stall.case_id, use_llm=False)
    elif stall.remediation == "retry_execution":
        from agentx import engine
        engine.advance(conn, stall.case_id, max_steps=1, use_llm=False)
    # flag_for_human never reaches here — `assess()` refuses it first.


def sweep(conn, *, workspace: str = "default", apply: bool = False,
          limit: int = 100, as_of: str | None = None) -> dict:
    """Scan, propose, and (optionally) heal. The whole loop in one call."""
    stalls = scan(conn, workspace=workspace, limit=limit, as_of=as_of)
    remedies = [heal(conn, s, apply=apply, as_of=as_of) for s in stalls]
    by_severity: dict[str, int] = {}
    for s in stalls:
        by_severity[s.severity] = by_severity.get(s.severity, 0) + 1
    return {
        "scanned_workspace": workspace,
        "stalls": [s.as_dict() for s in stalls],
        "remedies": [r.as_dict() for r in remedies],
        "by_severity": by_severity,
        "healed": sum(1 for r in remedies if r.status == HEALED),
        "needs_human": sum(1 for r in remedies if r.status == NEEDS_HUMAN),
        "applied": apply,
        "healthy": not stalls,
    }
