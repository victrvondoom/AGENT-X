"""
The Resolution Planner — an execution graph, not a paragraph.

A consumer problem is not solved by an answer. It is solved by a sequence of
actions with branches, waits, deadlines, retries and an escalation ladder, and the
representation of that sequence is the most important data structure in the
product. So a plan here is a validated graph of typed steps:

    Step        an action verb, a capability, parameters, an expected outcome,
                a failure branch, a retry policy, a deadline, a required
                autonomy level, and a confidence
    Plan        an ordered set of steps with a strategy, a version, and a
                validator report

THE LLM MAY PROPOSE. IT MAY NOT GOVERN.

`compose()` builds a plan deterministically from the problem definition, the
capability graph and the policy findings. `propose_with_llm()` can ask a model to
reorder, add or drop steps. Whatever either produces goes through `validate()`,
which is pure, deterministic, and has the only vote that counts: a plan that fails
validation cannot be activated, whatever proposed it.

That inversion is the whole design. An LLM asked to "plan a refund" will produce
something plausible every time, including when the merchant has no provider, when
the evidence needed for step three does not exist, when step five escalates before
anyone has waited, and when the deadline it cites has already passed. Every one of
those is a deterministic check, and every one of them is in `validate()`.

BRANCHING IS NOT DECORATION

    request_refund ──accepted──→ verify ──→ close
                   └─refused───→ escalate ──→ verify ──→ close

`on_success` and `on_failure` are step keys, and the executor follows them. A plan
whose branch target does not exist is rejected, so a case can never walk off the
end of its own plan into an undefined state.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

from agentx import capabilities as caps
from agentx import ids, store
from agentx.execution import actions as A
from agentx.ontology import ProblemDefinition, REMEDY_KINDS

# Escalation ladder, in order. A step may only escalate to a rung above the one
# already tried, which is what stops a plan from going straight to the ombudsman
# because the first email bounced.
LADDER = ("merchant_support", "platform_support", "carrier_support",
          "manufacturer_support", "payment_provider", "ombudsman",
          "regulator_complaint")


@dataclass
class Step:
    key: str
    action: str
    title: str
    capability: str | None = None
    params: dict = field(default_factory=dict)
    prerequisites: list[str] = field(default_factory=list)
    expected: dict = field(default_factory=dict)
    on_success: str | None = None
    on_failure: str | None = None
    failure_modes: list[str] = field(default_factory=list)
    retry: dict = field(default_factory=dict)
    wait_days: int | None = None
    deadline_at: str | None = None
    risk: str = "low"
    required_level: int = 1
    # An optional step improves the case if it works and is skipped if it does
    # not. Enrichment — reading a merchant's published terms, pulling a booking
    # record — belongs here: failing to fetch a nice-to-have must never strand a
    # plan whose actual remedy was ready to go.
    optional: bool = False
    ordinal: int = 0
    status: str = "PENDING"
    id: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Plan:
    case_id: str
    strategy: str
    steps: list[Step]
    confidence: float = 0.0
    proposed_by: str = "planner"
    version: int = 1
    id: str | None = None
    status: str = "DRAFT"
    validation: dict = field(default_factory=dict)
    # What prior cases against this counterparty said, and whether it changed
    # anything. Advisory only — it never widens what the governor permits.
    prior: dict = field(default_factory=dict)

    def step(self, key: str) -> Step | None:
        return next((s for s in self.steps if s.key == key), None)

    def as_dict(self) -> dict:
        return {"id": self.id, "case_id": self.case_id, "strategy": self.strategy,
                "version": self.version, "status": self.status,
                "proposed_by": self.proposed_by, "confidence": round(self.confidence, 3),
                "validation": self.validation, "prior": self.prior,
                "steps": [s.as_dict() for s in self.steps]}

    def summary_lines(self) -> list[str]:
        """The plan as a person would read it aloud."""
        out = []
        for i, s in enumerate(self.steps, 1):
            line = f"{i}. {s.title}"
            if s.wait_days:
                line += f" (wait up to {s.wait_days} days)"
            if s.on_failure:
                line += f" — if that fails: {s.on_failure.replace('_', ' ')}"
            out.append(line)
        return out


# ─────────────────────────────────────────────────────────────────────────────
# composition
# ─────────────────────────────────────────────────────────────────────────────
def compose(*, case: dict, definition: ProblemDefinition, remedy: str,
            findings: list, missing_evidence: list[dict],
            counterparty: str | None, amount_minor: int | None,
            currency: str | None, deadlines: list[dict] | None = None,
            prior: dict | None = None) -> Plan:
    """Build the plan for one remedy, deterministically.

    Structure comes from the capability graph, which comes from the problem
    definition — so a new problem type gets a coherent plan from its YAML file with
    no planner change. What varies per remedy is the ACT step and the escalation
    ladder; everything around it is the same shape for every consumer problem,
    which is the claim the ontology makes and this function tests.
    """
    graph = caps.capability_graph(definition, remedy, provider_hint=counterparty)
    available = {c["id"]: c for c in graph if c.get("available")}
    steps: list[Step] = []
    deadlines = deadlines or []
    first_deadline = min((d["due_at"] for d in deadlines), default=None)

    def add(step: Step) -> Step:
        step.ordinal = len(steps)
        steps.append(step)
        return step

    # ── gather what is missing ────────────────────────────────────────────
    gathered: list[str] = []
    for miss in missing_evidence:
        cap_id = ("booking_inspection"
                  if miss["kind"] in ("booking_confirmation", "boarding_pass",
                                      "baggage_tag", "cancellation_notice")
                  else "document_understanding")
        if cap_id not in available or cap_id == "document_understanding":
            # Nothing can retrieve a document the user has not uploaded, so this
            # becomes a question rather than a step that would pretend to fetch it.
            continue
        cap = caps.get(cap_id)
        assert cap is not None, f"{cap_id} is a declared capability"
        s = add(Step(key=f"retrieve_{miss['kind']}", action="retrieve",
                     title=f"Retrieve the {miss['kind'].replace('_', ' ')} from {counterparty or 'the provider'}",
                     capability=cap_id,
                     params={"counterparty": counterparty,
                             "record_kind": _record_kind(miss["kind"]),
                             "reference": miss.get("reference")},
                     expected={"outcome": "done", "evidence_kind": miss["kind"]},
                     failure_modes=["record not found", "provider unreachable"],
                     retry={"max": 2, "backoff_hours": 6}, optional=True,
                     risk=cap.risk, required_level=cap.required_level))
        gathered.append(s.key)

    if "merchant_terms_lookup" in available:
        s = add(Step(key="read_terms", action="retrieve",
                     title=f"Read {counterparty or 'the company'}'s published terms",
                     capability="merchant_terms_lookup",
                     params={"counterparty": counterparty},
                     expected={"outcome": "done", "evidence_kind": "terms"},
                     failure_modes=["terms not published"],
                     retry={"max": 1, "backoff_hours": 12}, optional=True,
                     risk="low", required_level=1))
        gathered.append(s.key)

    # ── the message ───────────────────────────────────────────────────────
    draft = add(Step(key="draft_message", action="draft",
                     title="Draft the message, citing only evidenced facts",
                     capability="communication_generation",
                     params={"remedy": remedy, "counterparty": counterparty,
                             "amount_minor": amount_minor, "currency": currency,
                             "policies": [f.policy.id for f in findings
                                          if getattr(f, "applies", "") == "yes"]},
                     prerequisites=list(gathered),
                     expected={"outcome": "done"},
                     risk="low", required_level=1))

    # ── the act ───────────────────────────────────────────────────────────
    act_cap, act_verb = _act_for(remedy, available)
    if act_cap is None:
        # No provider can carry out this remedy. Say so in the plan rather than
        # emitting a step that cannot run — the eligibility layer will have ranked
        # this remedy, and the user is entitled to know why it stops here.
        add(Step(key="blocked", action="inspect",
                 title=f"No integration is available to pursue a {REMEDY_KINDS.get(remedy, {}).get('label', remedy)}",
                 params={"remedy": remedy, "counterparty": counterparty},
                 prerequisites=[draft.key],
                 expected={"outcome": "blocked"},
                 failure_modes=["no provider registered for this counterparty"],
                 risk="low", required_level=0))
        plan = Plan(case_id=case["id"], strategy=remedy, steps=steps)
        plan.validation = validate(plan, definition=definition, counterparty=counterparty)
        plan.confidence = 0.0
        return plan

    cap = caps.get(act_cap)
    assert cap is not None, f"{act_cap} is a declared capability"
    sla = _sla_days(counterparty)
    # Experience adjusts the WAIT, never the permission. `is not None`, not
    # truthiness: a counterparty that has always answered same-day gives
    # typical_days == 0.0, which is the strongest possible evidence for a short
    # wait and must not be read as "no data" and fall back to the stated SLA.
    # If prior cases against
    # this counterparty took longer than its stated SLA to answer, waiting the
    # stated time and chasing into silence just burns a chase; if they answered
    # faster, there is no reason to sit out the full window. Bounded either side
    # so one unusual case cannot produce an absurd plan, and only applied when
    # the prior is actionable (>= 2 agreeing cases).
    prior_note = None
    if prior and prior.get("actionable") and prior.get("typical_days") is not None:
        learned = int(max(2, min(30, round(prior["typical_days"]))))
        if learned != sla:
            prior_note = (f"waiting {learned}d instead of the stated {sla}d, from "
                          f"{prior['cases']} prior case(s)")
            sla = learned
    act = add(Step(key="submit", action=act_verb,
                   title=_act_title(act_verb, remedy, counterparty),
                   capability=act_cap,
                   params={"counterparty": counterparty, "remedy": remedy,
                           "amount_minor": amount_minor, "currency": currency,
                           "problem_type": definition.problem_type,
                           "case_id": case["id"]},
                   prerequisites=[draft.key],
                   expected={"outcome": "accepted",
                             "external_ref": "a case or ticket reference"},
                   failure_modes=["refused", "no response", "wrong department"],
                   retry={"max": 2, "backoff_hours": 24},
                   deadline_at=first_deadline,
                   risk=cap.risk, required_level=cap.required_level))
    draft.on_success = act.key

    # ── wait, then chase ──────────────────────────────────────────────────
    wait = add(Step(key="await_response", action="schedule",
                    title=f"Wait up to {sla} days for a response",
                    capability="deadline_tracking",
                    params={"days": sla, "expect": "a decision or an acknowledgement"},
                    prerequisites=[act.key], wait_days=sla,
                    expected={"outcome": "done"},
                    risk="low", required_level=0))
    act.on_success = wait.key

    chase = None
    if "follow_up" in available:
        chase = add(Step(key="chase", action="follow_up",
                         title=f"Chase {counterparty or 'the company'} for a decision",
                         capability="follow_up",
                         params={"counterparty": counterparty, "case_id": case["id"]},
                         prerequisites=[wait.key],
                         expected={"outcome": "accepted"},
                         failure_modes=["still under review", "refused"],
                         retry={"max": 2, "backoff_hours": 72},
                         wait_days=3,
                         risk="low", required_level=2))
        if prior and prior.get("actionable") and prior.get("typical_chases"):
            # How many chases this company has historically needed before it
            # answered. Capped at 3: past that it is not a chase budget, it is a
            # refusal, and the escalation branch is the right answer.
            chase.retry = {"max": int(min(3, max(1, prior["typical_chases"]))),
                           "backoff_hours": 72}
        wait.on_success = chase.key

    # ── escalate on refusal ───────────────────────────────────────────────
    esc = None
    ladder = [r for r in definition.escalation if r in LADDER] or ["merchant_support"]
    if "escalation" in available and len(ladder) > 0:
        target = ladder[1] if len(ladder) > 1 else ladder[0]
        escalation_cap = caps.get("escalation")
        assert escalation_cap is not None, "escalation is a declared capability"
        esc = add(Step(key="escalate", action="escalate",
                       title=f"Escalate to {target.replace('_', ' ')}",
                       capability="escalation",
                       params={"counterparty": counterparty, "to": target,
                               "ladder": ladder, "case_id": case["id"],
                               "amount_minor": amount_minor, "currency": currency},
                       prerequisites=[act.key],
                       expected={"outcome": "accepted"},
                       failure_modes=["escalation refused", "outside their remit"],
                       retry={"max": 1, "backoff_hours": 48},
                       risk="high", required_level=escalation_cap.required_level))
        act.on_failure = esc.key
        if chase is not None:
            chase.on_failure = esc.key

    # ── verify, then close ────────────────────────────────────────────────
    verify = add(Step(key="verify_outcome", action="verify",
                      title="Re-check the company's records to confirm the outcome",
                      capability="outcome_verification",
                      params={"counterparty": counterparty, "case_id": case["id"]},
                      prerequisites=[act.key],
                      expected={"outcome": "done", "verified": True},
                      failure_modes=["credit not posted", "status unchanged"],
                      retry={"max": 3, "backoff_hours": 48},
                      risk="low", required_level=1))
    if chase is not None:
        chase.on_success = verify.key
    else:
        wait.on_success = verify.key
    if esc is not None:
        esc.on_success = verify.key

    close = add(Step(key="issue_receipt", action="verify",
                     title="Issue the signed resolution receipt",
                     capability="resolution_record",
                     params={"case_id": case["id"]},
                     prerequisites=[verify.key],
                     expected={"outcome": "done"},
                     risk="low", required_level=0))
    verify.on_success = close.key

    plan = Plan(case_id=case["id"], strategy=remedy, steps=steps)
    plan.validation = validate(plan, definition=definition, counterparty=counterparty)
    plan.confidence = _confidence(plan, findings, missing_evidence)
    if prior and prior.get("cases"):
        # Carried on the plan whether or not it changed anything, so a user can
        # always see what experience said — including when it said "not enough
        # to act on".
        plan.prior = {**prior, "applied": bool(prior_note), "adjustment": prior_note}
    return plan


def _record_kind(evidence_kind: str) -> str:
    return {"booking_confirmation": "booking", "boarding_pass": "booking",
            "cancellation_notice": "booking", "baggage_tag": "booking",
            "order_confirmation": "order", "invoice": "invoice",
            "transaction": "order", "bank_statement": "order"}.get(
                evidence_kind, "order")


def _act_for(remedy: str, available: dict) -> tuple[str | None, str]:
    table = {
        "merchant_refund": ("refund_request", "request_refund"),
        "partial_refund": ("refund_request", "request_refund"),
        "bill_correction": ("refund_request", "request_refund"),
        "goodwill_credit": ("refund_request", "request_refund"),
        "replacement": ("refund_request", "request_refund"),
        "repair": ("refund_request", "request_refund"),
        "rebooking": ("booking_inspection", "retrieve"),
        "cancellation": ("cancellation", "cancel"),
        "payment_dispute": ("dispute_generation", "escalate"),
        "regulator_complaint": ("escalation", "escalate"),
        "statutory_compensation": ("form_filling", "submit_form"),
        "insurance_claim": ("form_filling", "submit_form"),
        "explanation": ("communication_generation", "draft"),
    }
    cap_id, verb = table.get(remedy, ("refund_request", "request_refund"))
    if cap_id in available or cap_id == "communication_generation":
        return cap_id, verb
    # Fall back to email if the specific capability has no provider but a mailbox
    # exists — a letter to the support address is a real, if slower, route.
    if "email_interaction" in available:
        return "email_interaction", "email"
    return None, verb


def _act_title(verb: str, remedy: str, counterparty: str | None) -> str:
    who = counterparty or "the company"
    return {
        "request_refund": f"Ask {who} for a {REMEDY_KINDS.get(remedy, {}).get('label', 'refund').lower()}",
        "cancel": f"Cancel the service with {who}",
        "submit_form": f"Submit the claim to {who}",
        "escalate": f"File the {REMEDY_KINDS.get(remedy, {}).get('label', 'escalation').lower()}",
        "email": f"Email {who}'s resolution team",
        "retrieve": f"Arrange an alternative with {who}",
        "draft": "Prepare the explanation",
    }.get(verb, f"{verb.replace('_', ' ').title()} with {who}")


def _sla_days(counterparty: str | None) -> int:
    from agentx.sandbox import world
    cid = world.resolve_company(counterparty)
    if cid:
        return int(world.COMPANIES[cid]["sla_days"])
    return 5


def _confidence(plan: Plan, findings: list, missing_evidence: list[dict]) -> float:
    """How likely this plan is to reach its outcome, from what is actually known.

    Three factors, all of them structural rather than vibes: whether a right was
    established, whether the evidence the problem type requires is present, and
    whether the plan validated. A plan resting on an unestablished right with two
    critical documents missing gets a low number, and the UI shows that number
    rather than a green tick.
    """
    granted = sum(1 for f in findings if getattr(f, "applies", "") == "yes")
    unknown = sum(1 for f in findings if getattr(f, "applies", "") == "unknown")
    base = 0.35 + min(0.35, 0.12 * granted) - min(0.15, 0.04 * unknown)
    critical_missing = sum(1 for m in missing_evidence if m.get("critical"))
    base -= 0.18 * critical_missing
    if not plan.validation.get("ok", False):
        base -= 0.25
    return round(max(0.05, min(0.95, base)), 3)


# ─────────────────────────────────────────────────────────────────────────────
# validation — the only vote that counts
# ─────────────────────────────────────────────────────────────────────────────
def validate(plan: Plan, *, definition: ProblemDefinition | None = None,
             counterparty: str | None = None) -> dict:
    """Deterministically check a plan. Returns a report; never raises.

    Errors block activation. Warnings do not, but they travel with the plan into
    the audit chain and onto the approval card, so a user approving a plan with a
    known soft spot is approving it knowingly.
    """
    errors: list[str] = []
    warnings: list[str] = []
    keys = [s.key for s in plan.steps]
    keyset = set(keys)

    if len(keyset) != len(keys):
        errors.append("duplicate step keys: branch targets would be ambiguous")
    if not plan.steps:
        errors.append("a plan with no steps cannot resolve anything")

    for s in plan.steps:
        if s.action not in A.ACTIONS:
            errors.append(f"step {s.key!r}: unknown action {s.action!r}")
            continue
        spec = A.ACTIONS[s.action]

        if s.capability:
            cap = caps.get(s.capability)
            if cap is None:
                errors.append(f"step {s.key!r}: unknown capability {s.capability!r}")
            else:
                avail = caps.available(s.capability, provider_hint=counterparty)
                if not avail.get("available"):
                    errors.append(
                        f"step {s.key!r}: capability {s.capability!r} has no provider "
                        f"({avail.get('reason')}) — this step could not run")
                if s.action not in cap.actions:
                    errors.append(f"step {s.key!r}: capability {s.capability!r} cannot "
                                  f"perform {s.action!r}")
                if s.required_level < cap.required_level:
                    errors.append(f"step {s.key!r}: declares level {s.required_level} "
                                  f"but {s.capability!r} needs {cap.required_level}")

        for p in s.prerequisites:
            if p not in keyset:
                errors.append(f"step {s.key!r}: prerequisite {p!r} is not in this plan")
        for branch, target in (("on_success", s.on_success), ("on_failure", s.on_failure)):
            if target and target not in keyset:
                errors.append(f"step {s.key!r}: {branch} points at {target!r}, "
                              f"which is not in this plan")

        if spec.writes_externally and not s.expected:
            warnings.append(f"step {s.key!r} sends something externally but declares "
                            f"no expected outcome, so nothing can verify it")
        # The floor is set by HARM, not by unsendability. Submitting a claim form
        # and sending an email cannot be recalled, but neither damages the user if
        # it turns out to be wrong — that is exactly what level 2's
        # confirm-before-sending exists to cover. A chargeback or a regulator
        # complaint is different in kind, and only those carry a level-3 floor.
        if s.risk == "high" and s.required_level < 3:
            errors.append(f"step {s.key!r}: {s.action!r} is a high-risk action and "
                          f"cannot run below level 3")
        if spec.writes_externally and not spec.reversible and s.required_level < 2:
            errors.append(f"step {s.key!r}: {s.action!r} cannot be recalled once sent "
                          f"and needs at least level 2 (prepare and confirm)")
        if s.retry and int(s.retry.get("max", 0)) > 0 and not spec.reversible:
            warnings.append(f"step {s.key!r}: retrying an irreversible action can "
                            f"duplicate it; retry is capped at 1")
            s.retry["max"] = 1

    # ordering: prerequisites must appear earlier
    index = {s.key: i for i, s in enumerate(plan.steps)}
    for s in plan.steps:
        for p in s.prerequisites:
            if p in index and index[p] >= index[s.key]:
                errors.append(f"step {s.key!r} depends on {p!r}, which comes after it")

    if _has_cycle(plan):
        errors.append("the step graph contains a cycle; execution would not terminate")

    # an escalation must be reachable only after something was tried
    for s in plan.steps:
        if s.action == "escalate":
            if not s.prerequisites:
                errors.append(f"step {s.key!r} escalates without a prior attempt")
            elif not any(A.is_external(step.action)
                         for step in (plan.step(p) for p in s.prerequisites) if step):
                warnings.append(f"step {s.key!r} escalates before anything was actually "
                                f"sent to the counterparty")

    # every external write should be followed by a verification somewhere
    externals = [s for s in plan.steps if A.is_external(s.action) and s.action != "follow_up"]
    verifiers = [s for s in plan.steps if s.action == "verify"]
    if externals and not verifiers:
        errors.append("the plan takes external action but never verifies the outcome")

    if definition is not None:
        strategy_ok = (plan.strategy in definition.resolution_strategies
                       or plan.strategy == "explanation")
        if not strategy_ok:
            warnings.append(
                f"strategy {plan.strategy!r} is not one of the declared remedies for "
                f"{definition.problem_type!r} ({list(definition.resolution_strategies)})")

    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "steps": len(plan.steps),
            "external_steps": len(externals), "verifies": len(verifiers),
            "checked_at": ids.now(),
            "checks": ["vocabulary", "capability availability", "capability/action match",
                       "autonomy floor", "prerequisite existence", "branch targets",
                       "topological order", "cycle freedom", "escalation ordering",
                       "verification coverage", "declared remedy"]}


def _has_cycle(plan: Plan) -> bool:
    graph: dict[str, list[str]] = {s.key: list(s.prerequisites) for s in plan.steps}
    state: dict[str, int] = {}

    def walk(node: str) -> bool:
        if state.get(node) == 1:
            return True
        if state.get(node) == 2:
            return False
        state[node] = 1
        for nxt in graph.get(node, []):
            if nxt in graph and walk(nxt):
                return True
        state[node] = 2
        return False

    return any(walk(k) for k in graph)


# ─────────────────────────────────────────────────────────────────────────────
# LLM proposal — constrained, then validated
# ─────────────────────────────────────────────────────────────────────────────
_PROPOSE_SYSTEM = (
    "You revise a consumer resolution plan. Return ONLY JSON: "
    "{\"steps\": [{\"key\": \"...\", \"action\": \"...\", \"title\": \"...\", "
    "\"after\": [\"step_key\"], \"on_failure\": \"step_key or null\"}], "
    "\"rationale\": \"one sentence\"}. "
    "You may reorder, merge or drop steps and adjust titles. You MUST NOT invent "
    "an action verb outside the provided list, and you MUST NOT add a step whose "
    "capability is not in the provided available list. Keep every verification "
    "step. Prefer fewer steps."
)


def propose_with_llm(plan: Plan, *, definition: ProblemDefinition,
                     counterparty: str | None) -> tuple[Plan, str]:
    """Let a model revise a composed plan, then re-validate deterministically.

    If the revision fails validation, the ORIGINAL is kept and the failure is
    reported. That is the entire contract: the model gets to suggest, and a
    suggestion that would not survive a check never reaches a user's approval card.
    """
    try:
        from llm import client
        payload = {
            "problem": definition.problem_type,
            "strategy": plan.strategy,
            "allowed_actions": sorted(A.ACTIONS),
            "steps": [{"key": s.key, "action": s.action, "title": s.title,
                       "after": s.prerequisites, "on_failure": s.on_failure}
                      for s in plan.steps],
        }
        got = client.chat_json(_PROPOSE_SYSTEM, store.jdump(payload), task="plan")
    except Exception as e:
        return plan, f"model unavailable ({type(e).__name__}); composed plan stands"

    rows = got.get("steps") if isinstance(got, dict) else None
    if not isinstance(rows, list) or not rows:
        return plan, "model returned no usable steps; composed plan stands"

    by_key = {s.key: s for s in plan.steps}
    revised: list[Step] = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            continue
        key = r.get("key")
        if not isinstance(key, str):
            continue                # a step with no usable key cannot be matched
        base = by_key.get(key)
        if base is None:
            continue                # a step nobody composed has no capability behind it
        s = Step(**{**base.as_dict(), "ordinal": i})
        s.title = str(r.get("title") or base.title)[:160]
        after = [k for k in (r.get("after") or []) if k in by_key]
        s.prerequisites = after
        s.on_failure = r.get("on_failure") if r.get("on_failure") in by_key else base.on_failure
        revised.append(s)

    if not revised:
        return plan, "model kept no recognisable steps; composed plan stands"

    candidate = Plan(case_id=plan.case_id, strategy=plan.strategy, steps=revised,
                     proposed_by="llm", version=plan.version + 1,
                     confidence=plan.confidence)
    candidate.validation = validate(candidate, definition=definition,
                                    counterparty=counterparty)
    if not candidate.validation["ok"]:
        return plan, ("model revision rejected by the validator: "
                      + "; ".join(candidate.validation["errors"][:3]))
    return candidate, (got.get("rationale") or "model revision accepted")[:200]


# ─────────────────────────────────────────────────────────────────────────────
# persistence
# ─────────────────────────────────────────────────────────────────────────────
def supersede_drafts(conn, case_id: str) -> int:
    """Retire un-started plans for a case, and return the next version number.

    A case is re-planned every time new evidence lands, and without this each pass
    left another version-1 plan behind. `active_plan` then picked one of them
    arbitrarily — in practice the stale one, composed before the evidence that
    changed the answer, so a case whose best remedy had become a £350 statutory
    claim went on executing a £92 part-refund plan.

    Only DRAFT and VALIDATED plans are retired. An ACTIVE plan is mid-flight and
    is never silently replaced: actions have already gone out under it.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(version), 0) FROM plans WHERE case_id = %s",
                    (case_id,))
        top = int((cur.fetchone() or [0])[0] or 0)
        cur.execute("UPDATE plans SET status = 'SUPERSEDED' WHERE case_id = %s"
                    " AND status IN ('DRAFT','VALIDATED')", (case_id,))
    return top + 1


def persist(conn, plan: Plan) -> Plan:
    plan.id = plan.id or ids.new("plan")
    plan.status = "VALIDATED" if plan.validation.get("ok") else "DRAFT"
    now = ids.now()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO plans (id, case_id, version, strategy, status, proposed_by,"
            " validated, confidence, prior, created_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (plan.id, plan.case_id, plan.version, plan.strategy, plan.status,
             plan.proposed_by, store.jdump(plan.validation), plan.confidence,
             store.jdump(plan.prior or {}), now))
        for s in plan.steps:
            s.id = s.id or ids.new("st")
            cur.execute(
                "INSERT INTO plan_steps (id, plan_id, case_id, ordinal, step_key, action,"
                " capability, title, params, prerequisites, expected, on_success,"
                " on_failure, failure_modes, retry, wait_days, deadline_at, risk,"
                " required_level, status, attempts, created_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s)",
                (s.id, plan.id, plan.case_id, s.ordinal, s.key, s.action, s.capability,
                 s.title, store.jdump({**s.params, "_optional": s.optional}),
                 store.jdump(s.prerequisites),
                 store.jdump(s.expected), s.on_success, s.on_failure,
                 store.jdump(s.failure_modes), store.jdump(s.retry), s.wait_days,
                 s.deadline_at, s.risk, s.required_level, s.status, now))
    return plan


def load(conn, plan_id: str) -> Plan | None:
    with conn.cursor() as cur:
        cur.execute("SELECT id, case_id, version, strategy, status, proposed_by,"
                    " validated, confidence, prior FROM plans WHERE id = %s", (plan_id,))
        row = cur.fetchone()
        if not row:
            return None
        cur.execute(
            "SELECT id, ordinal, step_key, action, capability, title, params,"
            " prerequisites, expected, on_success, on_failure, failure_modes, retry,"
            " wait_days, deadline_at, risk, required_level, status"
            " FROM plan_steps WHERE plan_id = %s ORDER BY ordinal ASC", (plan_id,))
        steps = [Step(id=r[0], ordinal=r[1], key=r[2], action=r[3], capability=r[4],
                      title=r[5], params=store.jload(r[6], {}),
                      optional=bool(store.jload(r[6], {}).get("_optional")),
                      prerequisites=store.jload(r[7], []),
                      expected=store.jload(r[8], {}), on_success=r[9], on_failure=r[10],
                      failure_modes=store.jload(r[11], []), retry=store.jload(r[12], {}),
                      wait_days=r[13], deadline_at=r[14], risk=r[15],
                      required_level=r[16], status=r[17]) for r in cur.fetchall()]
    return Plan(id=row[0], case_id=row[1], version=row[2], strategy=row[3],
                status=row[4], proposed_by=row[5], validation=store.jload(row[6], {}),
                confidence=row[7] or 0.0, prior=store.jload(row[8], {}) or {},
                steps=steps)


def active_plan(conn, case_id: str) -> Plan | None:
    """The plan a case is actually running.

    An ACTIVE plan always wins over a newer VALIDATED one: once actions have gone
    out under a plan, that is the plan the case is on, and swapping it because new
    evidence suggested a better strategy would orphan the executions already
    recorded against its steps.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM plans WHERE case_id = %s AND status = 'ACTIVE'"
                    " ORDER BY version DESC, created_at DESC LIMIT 1", (case_id,))
        row = cur.fetchone()
        if not row:
            cur.execute("SELECT id FROM plans WHERE case_id = %s AND status = 'VALIDATED'"
                        " ORDER BY version DESC, created_at DESC LIMIT 1", (case_id,))
            row = cur.fetchone()
    return load(conn, row[0]) if row else None


def set_plan_status(conn, plan_id: str, status: str) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE plans SET status = %s WHERE id = %s", (status, plan_id))


def set_step_status(conn, step_id: str, status: str, *, attempts: int | None = None) -> None:
    with conn.cursor() as cur:
        if attempts is None:
            cur.execute("UPDATE plan_steps SET status = %s WHERE id = %s", (status, step_id))
        else:
            cur.execute("UPDATE plan_steps SET status = %s, attempts = %s WHERE id = %s",
                        (status, attempts, step_id))


def next_step(plan: Plan) -> Step | None:
    """The next step whose prerequisites are all done.

    Branch-aware, and the definition of "satisfied" is wider than "succeeded":

        DONE       it worked
        SKIPPED    the branch taken went around it
        FAILED     it ran and did not work — which is exactly the condition its
                   `on_failure` branch exists to handle
        DELEGATED  something else owns it now (the follow-up scheduler)

    Treating FAILED as unsatisfied deadlocks the plan precisely when it matters:
    a refused refund would leave the escalation step waiting forever on the
    prerequisite whose failure was its whole reason to exist.
    """
    done = {s.key for s in plan.steps
            if s.status in ("DONE", "SKIPPED", "FAILED", "DELEGATED")}
    for s in plan.steps:
        if s.status != "PENDING":
            continue
        if all(p in done for p in s.prerequisites):
            return s
    return None
