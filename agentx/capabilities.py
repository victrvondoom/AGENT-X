"""
The capability registry — what Agent X can actually do, declared once.

A classifier that returns a label leaves the hard part undone. Knowing a case is a
`duplicate_charge` is worth nothing until something decides which of Agent X's
abilities to compose, in what order, under whose authority. So classification here
routes into CAPABILITIES, and this registry is the set they are drawn from:

    Problem → Intent / Domain / Problem type / Risk / Autonomy
            → Capability graph
            → Resolution plan

Each capability declares four things the planner and the governor need and cannot
infer:

    phase           where in the resolution lifecycle it belongs
    actions         which standardised action verbs it can perform
    provider        which provider family must be available for it to run at all
    required_level  the autonomy level a case must have reached to use it

`required_level` is the load-bearing one. It is declared next to the capability
rather than checked at the call site, because a capability whose risk lives in an
`if` statement somewhere in the executor is a capability whose risk is invisible
to the person approving it. Declared here, it ships in the plan, in the approval
card, and in the signed receipt.

AVAILABILITY IS NOT ASSUMED

`available()` asks the provider registry whether anything can actually serve this
capability right now, and in which mode. A capability with no provider is reported
as unavailable and the planner routes around it — it never produces a plan step
that would quietly do nothing. This is the mechanism behind the product rule that
Agent X never claims an integration it does not have.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

# The resolution lifecycle, in order. A plan's steps are sorted by phase before
# dependencies are applied, so a plan can never verify an outcome before the
# action that produces it.
PHASES = ("understand", "gather", "analyse", "decide", "act", "await", "verify", "close")


@dataclass(frozen=True)
class Capability:
    id: str
    label: str
    phase: str
    summary: str
    actions: tuple[str, ...]
    provider_family: str | None = None     # None = no external system needed
    required_level: int = 1
    risk: str = "low"
    reversible: bool = True
    produces_evidence: bool = False
    consumes: tuple[str, ...] = ()          # evidence kinds or fact predicates it needs
    produces: tuple[str, ...] = ()          # fact predicates it can fill

    def as_dict(self) -> dict:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# the registry
# ─────────────────────────────────────────────────────────────────────────────
_CAPS: tuple[Capability, ...] = (
    Capability(
        id="problem_understanding", label="Understand the problem", phase="understand",
        summary="Turn a sentence into a distribution over problem types, with entities.",
        actions=("inspect",), required_level=0, risk="low",
        produces=("case.problem_type", "case.domain")),

    Capability(
        id="document_understanding", label="Read the documents", phase="gather",
        summary="Extract typed, located facts from receipts, invoices, statements and emails.",
        actions=("retrieve", "inspect"), required_level=0, risk="low",
        produces_evidence=False,
        consumes=("receipt", "invoice", "bank_statement", "transaction", "email"),
        produces=("invoice.total", "charge.amount", "charge.date", "order.id")),

    Capability(
        id="booking_inspection", label="Inspect the booking", phase="gather",
        summary="Retrieve a reservation from the operating provider and read its live state.",
        actions=("retrieve", "inspect"), provider_family="booking", required_level=1,
        risk="low", produces_evidence=True,
        produces=("booking.status", "booking.rate", "booking.checkin", "flight.number")),

    Capability(
        id="transaction_analysis", label="Analyse the transactions", phase="analyse",
        summary="Compare charges for duplication, instalment shape, holds and reversals.",
        actions=("inspect",), required_level=0, risk="low",
        consumes=("charge.amount", "charge.date"),
        produces=("charge.duplicate_of", "charge.pattern")),

    Capability(
        id="evidence_extraction", label="Build the evidence graph", phase="gather",
        summary="Normalise every artefact into facts with provenance, and detect contradictions.",
        actions=("inspect",), required_level=0, risk="low"),

    Capability(
        id="policy_lookup", label="Find the applicable rules", phase="analyse",
        summary="Evaluate the statutory, scheme and contractual rules this case turns on.",
        actions=("retrieve", "inspect"), required_level=0, risk="low",
        produces=("case.policies",)),

    Capability(
        id="merchant_terms_lookup", label="Read the merchant's own terms", phase="analyse",
        summary="Retrieve the counterparty's published policy for this transaction type.",
        actions=("navigate", "retrieve"), provider_family="browser", required_level=1,
        risk="low", produces_evidence=True, produces=("merchant.policy",)),

    Capability(
        id="eligibility_determination", label="Decide what you are owed", phase="decide",
        summary="Turn applicable rules plus established facts into ranked, costed remedies.",
        actions=("inspect",), required_level=0, risk="low",
        produces=("case.remedies",)),

    Capability(
        id="communication_generation", label="Draft the message", phase="act",
        summary="Write the letter, citing only facts that trace to evidence.",
        actions=("draft",), required_level=1, risk="low", reversible=True),

    Capability(
        id="email_interaction", label="Send and read email", phase="act",
        summary="Send the message to the counterparty and watch for a reply.",
        actions=("email", "follow_up"), provider_family="email", required_level=2,
        risk="medium", reversible=False, produces_evidence=True),

    Capability(
        id="browser_interaction", label="Use the company's website", phase="act",
        summary="Navigate a self-service flow, read the page, and capture what it says.",
        actions=("navigate", "inspect", "submit_form"), provider_family="browser",
        required_level=2, risk="medium", reversible=False, produces_evidence=True),

    Capability(
        id="form_filling", label="Complete a claim form", phase="act",
        summary="Fill a structured form from established facts and submit it.",
        actions=("submit_form",), provider_family="browser", required_level=2,
        risk="medium", reversible=False, produces_evidence=True),

    Capability(
        id="refund_request", label="Request the refund", phase="act",
        summary="Ask the merchant to refund, through whatever channel it accepts.",
        actions=("request_refund",), provider_family="merchant", required_level=2,
        risk="medium", reversible=True, produces_evidence=True),

    Capability(
        id="cancellation", label="Cancel the service", phase="act",
        summary="End a subscription, booking or plan and capture the confirmation.",
        actions=("cancel",), provider_family="subscription", required_level=3,
        risk="medium", reversible=False, produces_evidence=True),

    Capability(
        id="dispute_generation", label="Raise a payment dispute", phase="act",
        summary="File a chargeback with the card issuer, with the evidence package attached.",
        actions=("escalate", "submit_form"), provider_family="payment", required_level=4,
        risk="high", reversible=False, produces_evidence=True),

    Capability(
        id="escalation", label="Escalate", phase="act",
        summary="Move the case up a level: supervisor, platform, regulator or ombudsman.",
        actions=("escalate",), provider_family="merchant", required_level=3,
        risk="high", reversible=False, produces_evidence=True),

    Capability(
        id="deadline_tracking", label="Track the deadlines", phase="await",
        summary="Record statutory and scheme windows and warn before they close.",
        actions=("schedule",), required_level=0, risk="low"),

    Capability(
        id="follow_up", label="Chase for a response", phase="await",
        summary="Re-contact the counterparty when a promised response does not arrive.",
        actions=("follow_up", "email"), provider_family="email", required_level=2,
        risk="low", produces_evidence=True),

    Capability(
        id="outcome_verification", label="Verify the outcome", phase="verify",
        summary="Re-check the external system for the state the action was supposed to produce.",
        actions=("verify", "retrieve"), provider_family="any", required_level=1,
        risk="low", produces_evidence=True,
        produces=("refund.received", "booking.status", "subscription.status")),

    Capability(
        id="resolution_record", label="Issue the resolution receipt", phase="close",
        summary="Seal the case: signed receipt, evidence package, verifiable chain head.",
        actions=("verify",), required_level=0, risk="low", produces_evidence=False),
)

REGISTRY: dict[str, Capability] = {c.id: c for c in _CAPS}


def get(cap_id: str) -> Capability | None:
    return REGISTRY.get(cap_id)


def by_phase(phase: str) -> list[Capability]:
    return [c for c in _CAPS if c.phase == phase]


def for_action(action: str) -> list[Capability]:
    return [c for c in _CAPS if action in c.actions]


# ─────────────────────────────────────────────────────────────────────────────
# availability
# ─────────────────────────────────────────────────────────────────────────────
def available(cap_id: str, *, provider_hint: str | None = None) -> dict:
    """Can this capability run right now, and in what mode?

    Answers with the provider registry, never with optimism. A capability whose
    provider family has no registered provider is `available: False`, and the
    planner will not emit a step for it — which is what keeps Agent X from
    producing a plan whose third step silently does nothing.
    """
    cap = REGISTRY.get(cap_id)
    if cap is None:
        return {"available": False, "reason": f"no such capability {cap_id!r}"}
    if cap.provider_family is None:
        return {"available": True, "mode": "internal",
                "reason": "runs inside Agent X; no external system involved"}

    from agentx.execution import providers
    return providers.availability(cap.provider_family, hint=provider_hint)


def capability_graph(definition, remedy: str | None = None,
                     *, provider_hint: str | None = None) -> list[dict]:
    """The ordered capabilities a case of this shape needs.

    Built from the problem definition rather than a per-vertical branch: the
    evidence requirements decide which gathering capabilities appear, the chosen
    remedy decides which acting capability appears, and the escalation ladder
    decides whether escalation is in the graph at all.

    Returns dicts rather than Capability objects because the availability verdict
    travels with each entry — the planner needs both, and separating them invites
    a plan built from a capability list that was checked a second earlier.
    """
    wanted: list[str] = ["problem_understanding", "evidence_extraction"]

    kinds = {r.kind for r in definition.required_evidence} | {
        alt for r in definition.required_evidence for alt in r.satisfied_by}
    if kinds & {"receipt", "invoice", "bank_statement", "transaction",
                "order_confirmation", "email", "policy_document", "warranty_document"}:
        wanted.append("document_understanding")
    if kinds & {"booking_confirmation", "boarding_pass", "baggage_tag",
                "cancellation_notice", "provider_record"}:
        wanted.append("booking_inspection")
    if definition.domain in ("finance", "billing") or "charge.amount" in definition.expected_facts:
        wanted.append("transaction_analysis")

    wanted += ["policy_lookup", "merchant_terms_lookup", "eligibility_determination"]

    act = {
        "merchant_refund": "refund_request",
        "partial_refund": "refund_request",
        "bill_correction": "refund_request",
        "goodwill_credit": "refund_request",
        "replacement": "refund_request",
        "repair": "refund_request",
        "rebooking": "booking_inspection",
        "cancellation": "cancellation",
        "payment_dispute": "dispute_generation",
        "regulator_complaint": "escalation",
        "statutory_compensation": "form_filling",
        "insurance_claim": "form_filling",
        "explanation": "communication_generation",
    }
    wanted.append("communication_generation")
    if remedy and act.get(remedy):
        wanted.append(act[remedy])
    # A letter to the published support address is the universal fallback route:
    # slower than an API, available for every counterparty, and the thing a person
    # would do. It belongs in the graph so the planner can fall back to it when a
    # specific integration is missing, rather than declaring the case unactionable.
    wanted.append("email_interaction")
    if definition.escalation:
        wanted.append("escalation")
    wanted += ["deadline_tracking", "follow_up", "outcome_verification",
               "resolution_record"]

    order = {p: i for i, p in enumerate(PHASES)}
    seen: set[str] = set()
    out: list[dict] = []
    for cid in wanted:
        if cid in seen or cid not in REGISTRY:
            continue
        seen.add(cid)
        cap = REGISTRY[cid]
        out.append({**cap.as_dict(), **available(cid, provider_hint=provider_hint)})
    out.sort(key=lambda c: (order.get(c["phase"], 99), c["required_level"]))
    return out


def summary() -> dict:
    """Registry snapshot for the API — capability, phase, risk, live availability."""
    return {
        "phases": list(PHASES),
        "capabilities": [
            {**c.as_dict(), **available(c.id)} for c in _CAPS
        ],
    }
