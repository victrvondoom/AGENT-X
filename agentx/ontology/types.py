"""
The consumer-problem ontology: the vocabulary every other subsystem speaks.

A consumer agent that hardcodes verticals ends up as a pile of if-statements —
one for airlines, one for hotels, one for Amazon — that share no reasoning and
cannot be extended without editing the application. The alternative is to name the
handful of things that are actually common across every consumer problem and make
the verticals data.

There are five such things, and they are the five types below:

    DOMAIN          which world the problem lives in (travel, commerce, telecom…)
    PROBLEM TYPE    what went wrong, normalised (duplicate_charge, baggage_lost…)
    ENTITY          the nouns a resolution needs to name (merchant, order, payment)
    EVIDENCE KIND   what would show it happened (transaction, receipt, booking…)
    REMEDY KIND     what "fixed" can mean (merchant_refund, payment_dispute…)

A new consumer problem type is then a YAML file: which domain, which entities,
which evidence, which remedies, which deadlines. No Python changes, no new
endpoint, no new page. That is the whole architectural bet of this layer, and
`agentx/ontology/definitions/` is the evidence it pays off.

Nothing here is an enum in the `enum.Enum` sense. These are frozen string sets,
because every one of these values crosses a JSON boundary into the audit chain and
into the UI, and a value that means one thing in Python and another in a stored
hash is precisely the drift this product exists to prevent.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

# ── domains ───────────────────────────────────────────────────────────────
# Deliberately coarse. Domains route to policy corpora and provider families;
# they are not a taxonomy for its own sake, and a domain that no policy or
# provider distinguishes does not earn a place here.
DOMAINS: dict[str, str] = {
    "travel":        "flights, rail, coach — carriage of a person",
    "hospitality":   "hotels, short lets, venues — accommodation",
    "commerce":      "buying goods from a merchant, online or in person",
    "delivery":      "the shipping leg of a purchase: carriers, tracking, doorstep",
    "subscriptions": "recurring digital or physical plans with a renewal cycle",
    "memberships":   "clubs, gyms, associations with a joining and leaving process",
    "telecom":       "mobile, broadband, landline — metered or bundled service",
    "utilities":     "energy, water, council services — regulated supply",
    "finance":       "cards, banks, wallets — the payment instrument itself",
    "insurance":     "policies and claims",
    "warranty":      "manufacturer or extended guarantees on goods",
    "services":      "tradespeople, repairs, professional services",
    "appointments":  "booked slots for a service, medical or otherwise",
    "billing":       "the invoice itself — clarity, accuracy, adjustment",
}

# ── entity kinds ──────────────────────────────────────────────────────────
# The nouns a resolution has to be able to name. If Agent X cannot fill the
# required entities for a problem type, it cannot act — it can only ask.
ENTITY_KINDS: dict[str, str] = {
    "merchant":      "the company the user dealt with",
    "provider":      "the operating company, where different from the seller",
    "order":         "an order or purchase reference",
    "payment":       "a specific transaction: amount, date, instrument",
    "card":          "the payment instrument (last four, network)",
    "account":       "the user's account with the merchant or provider",
    "booking":       "a reservation reference (PNR, confirmation code)",
    "subscription":  "a recurring plan",
    "policy_number": "an insurance or warranty policy reference",
    "shipment":      "a tracking reference",
    "product":       "the item purchased",
    "amount":        "a money value in dispute",
    "date":          "a date the problem hinges on",
    "person":        "the consumer, or a named counterparty",
    "location":      "an address, airport, or property",
    "case_ref":      "a reference the counterparty has already issued",
}

# ── evidence kinds ────────────────────────────────────────────────────────
# What could show the problem happened. `trust` is the default weight class of
# that kind of artefact — an issuer document outranks a user's screenshot when
# the two disagree, and the contradiction engine relies on this ordering.
EVIDENCE_KINDS: dict[str, dict] = {
    "transaction":       {"trust": "issuer_document", "label": "Bank or card transaction"},
    "bank_statement":    {"trust": "issuer_document", "label": "Bank statement"},
    "receipt":           {"trust": "issuer_document", "label": "Receipt"},
    "invoice":           {"trust": "issuer_document", "label": "Invoice or bill"},
    "order_confirmation": {"trust": "issuer_document", "label": "Order confirmation"},
    "booking_confirmation": {"trust": "issuer_document", "label": "Booking confirmation"},
    "cancellation_notice": {"trust": "issuer_document", "label": "Cancellation notice"},
    "boarding_pass":     {"trust": "issuer_document", "label": "Boarding pass"},
    "baggage_tag":       {"trust": "issuer_document", "label": "Baggage receipt"},
    "policy_document":   {"trust": "issuer_document", "label": "Policy or contract"},
    "warranty_document": {"trust": "issuer_document", "label": "Warranty document"},
    "terms":             {"trust": "third_party",     "label": "Terms and conditions"},
    "email":             {"trust": "third_party",     "label": "Email correspondence"},
    "chat_transcript":   {"trust": "third_party",     "label": "Chat transcript"},
    "screenshot":        {"trust": "user_capture",    "label": "Screenshot"},
    "photograph":        {"trust": "user_capture",    "label": "Photograph"},
    "order_page":        {"trust": "user_capture",    "label": "Order page capture"},
    "tracking":          {"trust": "third_party",     "label": "Tracking record"},
    "statement_note":    {"trust": "user_capture",    "label": "The user's own account"},
    "provider_record":   {"trust": "third_party",     "label": "Record retrieved from the provider"},
    "confirmation_page": {"trust": "third_party",     "label": "Confirmation page captured by Agent X"},
}

TRUST_ORDER = ["issuer_document", "third_party", "provider_record", "user_capture", "derived"]

# ── remedy kinds ──────────────────────────────────────────────────────────
# What "resolved" can mean. Each carries the risk class of pursuing it, which the
# governor turns into an authorisation requirement.
REMEDY_KINDS: dict[str, dict] = {
    "merchant_refund":      {"risk": "low",    "label": "Refund from the merchant"},
    "partial_refund":       {"risk": "low",    "label": "Partial refund or fee reversal"},
    "replacement":          {"risk": "low",    "label": "Replacement or reshipment"},
    "repair":               {"risk": "low",    "label": "Repair under warranty"},
    "rebooking":            {"risk": "medium", "label": "Rebooking or alternative arrangement"},
    "cancellation":         {"risk": "medium", "label": "Cancellation of the service"},
    "bill_correction":      {"risk": "low",    "label": "Corrected bill"},
    "goodwill_credit":      {"risk": "low",    "label": "Account credit"},
    "statutory_compensation": {"risk": "medium", "label": "Statutory compensation claim"},
    "insurance_claim":      {"risk": "medium", "label": "Claim on a policy"},
    "payment_dispute":      {"risk": "high",   "label": "Card scheme dispute / chargeback"},
    "regulator_complaint":  {"risk": "high",   "label": "Complaint to a regulator or ombudsman"},
    "explanation":          {"risk": "low",    "label": "An explanation, no money moves"},
}

RISK_LEVELS = ("low", "medium", "high")

# ── case states ───────────────────────────────────────────────────────────
# The state machine every case walks. Terminal states are listed separately
# because the follow-up scheduler must never wake a closed case.
CASE_STATES = (
    "OPEN",              # created, nothing understood yet
    "INVESTIGATING",     # gathering evidence and facts
    "NEEDS_INPUT",       # blocked on the user for the minimum information
    "ACTION_REQUIRED",   # a plan exists and needs authorisation
    "ACTION_SUBMITTED",  # an action executed against an external system
    "WAITING_EXTERNAL",  # the counterparty owes us a response
    "FOLLOW_UP_REQUIRED",  # the wait elapsed with no response
    "ESCALATED",         # moved up a level after a refusal or a timeout
    "RESOLVED",
    "CLOSED_UNRESOLVED",
    "WITHDRAWN",
)
TERMINAL_STATES = ("RESOLVED", "CLOSED_UNRESOLVED", "WITHDRAWN")


@dataclass(frozen=True)
class EvidenceRequirement:
    """One thing a problem type needs to see before it can be acted on.

    `critical` is the load-bearing field. A non-critical requirement missing lowers
    confidence; a critical one missing blocks execution entirely, and the planner
    turns it into a question rather than proceeding on an assumption.
    """
    kind: str
    why: str
    critical: bool = True
    satisfied_by: tuple[str, ...] = ()      # alternative evidence kinds that also count

    def accepts(self, kind: str) -> bool:
        return kind == self.kind or kind in self.satisfied_by


@dataclass(frozen=True)
class Discriminator:
    """A question whose answer separates rival interpretations.

    `favours` / `disfavours` name problem types, so answering one question
    reweights the whole hypothesis set at once instead of confirming a single
    guess. That is what stops the classifier from asking six questions to rule out
    six things.
    """
    id: str
    question: str
    kind: str = "choice"
    options: tuple[str, ...] = ()
    favours: dict[str, float] = field(default_factory=dict)
    disfavours: dict[str, float] = field(default_factory=dict)
    why: str = ""


@dataclass(frozen=True)
class DeadlineRule:
    label: str
    days: int
    kind: str = "scheme"          # statutory | scheme | merchant_sla | internal
    from_event: str = "incident"  # incident | first_contact | submission
    source: str = ""


@dataclass(frozen=True)
class ProblemDefinition:
    """A consumer problem type, declared rather than coded.

    Everything the engine needs to understand, gather for, reason about, plan and
    escalate a class of problem lives in one of these. Adding a vertical means
    adding one file.
    """
    problem_type: str
    domain: str
    label: str
    summary: str
    prior: float = 0.05
    ambiguity_group: str | None = None
    phrases: tuple[str, ...] = ()
    patterns: tuple[str, ...] = ()
    negative_phrases: tuple[str, ...] = ()
    group_triggers: tuple[str, ...] = ()
    required_entities: tuple[str, ...] = ()
    optional_entities: tuple[str, ...] = ()
    required_evidence: tuple[EvidenceRequirement, ...] = ()
    expected_facts: tuple[str, ...] = ()
    discriminators: tuple[Discriminator, ...] = ()
    policies: tuple[str, ...] = ()
    resolution_strategies: tuple[str, ...] = ()
    escalation: tuple[str, ...] = ()
    deadlines: tuple[DeadlineRule, ...] = ()
    risk: str = "medium"
    default_autonomy: int = 2
    provider_family: str | None = None      # which sandbox/live provider family serves it

    def critical_evidence(self) -> tuple[EvidenceRequirement, ...]:
        return tuple(r for r in self.required_evidence if r.critical)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["required_evidence"] = [asdict(r) for r in self.required_evidence]
        d["discriminators"] = [asdict(x) for x in self.discriminators]
        d["deadlines"] = [asdict(x) for x in self.deadlines]
        return d
