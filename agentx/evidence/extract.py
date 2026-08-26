"""
Evidence extraction — messy consumer artefacts into typed, located, scored facts.

The unit of output is a FACT CANDIDATE, and every one of them carries four things
that a plain "extracted JSON" blob does not:

    predicate   what kind of claim this is (charge.amount, booking.reference…)
    confidence  how much to believe it, on the scale the trust gate already uses
    method      deterministic | llm | provider | user_stated — how it was obtained
    locator     where in the source it came from, plus the excerpt

The locator is the reason this module exists rather than a prompt. A claim Agent X
makes to a user, to a merchant, or to a card issuer has to be traceable to a
specific line of a specific document, or it is an assertion with a citation-shaped
decoration on it. Everything downstream — the fact graph, the contradiction
engine, the evidence package, the resolution receipt — is built on that link
holding.

Two extraction paths, and the split is deliberate:

  * DETERMINISTIC extractors run first and own anything a regular expression can
    read reliably: money, dates, references, statuses. They are reproducible, they
    cost nothing, and a signed receipt built on them can be re-derived years later
    without a model that may no longer exist.
  * The LLM extractor runs only on what is left, is capped in confidence below the
    deterministic path, and is labelled as such in every record. It never overrides
    a deterministic fact; it can only add predicates nothing else found.

Confidence is then routed through `core.trust.gate` — the same per-field-type
threshold policy the document pipeline uses — so a low-confidence amount on a
consumer case is held to the same standard as a low-confidence total on an
invoice, and the routing decision ships in the audit chain.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

from core.trust import gate
from agentx import normalize
from agentx.ontology import EVIDENCE_KINDS

# What a money value MEANS depends on the artefact it was read from. The same
# "2,399" is a charge on a bank statement, an invoice total on a bill, and an
# order total on a confirmation — and conflating them is how a fee gets compared
# against a subtotal and reported as a discrepancy that does not exist.
MONEY_PREDICATE = {
    "transaction": "charge.amount",
    "bank_statement": "charge.amount",
    "receipt": "invoice.total",
    "invoice": "invoice.total",
    "order_confirmation": "order.total",
    "booking_confirmation": "booking.rate",
    "cancellation_notice": "booking.rate",
    "policy_document": "policy.excess",
    "warranty_document": "product.price",
    "screenshot": "quoted.amount",
    "order_page": "quoted.amount",
    "email": "stated.amount",
    "chat_transcript": "stated.amount",
    "statement_note": "stated.amount",
    "provider_record": "charge.amount",
    "confirmation_page": "charge.amount",
}
DATE_PREDICATE = {
    "transaction": "charge.date",
    "bank_statement": "charge.date",
    "receipt": "invoice.date",
    "invoice": "invoice.date",
    "order_confirmation": "order.purchased_at",
    "booking_confirmation": "booking.checkin",
    "cancellation_notice": "cancellation.at",
    "boarding_pass": "flight.scheduled_departure",
    "tracking": "shipment.delivered_at",
    "email": "message.date",
}
REF_PREDICATE = {
    "booking": "booking.reference",
    "order": "order.id",
    "shipment": "shipment.tracking",
    "policy_number": "policy.number",
    "case_ref": "case.external_ref",
}

# Confidence ceilings by method. A model reading a blurry screenshot must not be
# able to out-vote a regular expression reading a machine-generated statement,
# whatever the model says about itself.
METHOD_CEILING = {"deterministic": 0.96, "provider": 0.98, "llm": 0.72,
                  "user_stated": 0.60}

# Trust class multipliers. An issuer's own document is worth more than a
# screenshot of one, and both are worth more than a recollection.
TRUST_WEIGHT = {"issuer_document": 1.0, "provider_record": 0.95,
                "third_party": 0.85, "user_capture": 0.7, "derived": 0.75}

_STATUS_WORDS = [
    ("pending", "pending"), ("authorisation", "pending"), ("authorization", "pending"),
    ("processing", "pending"), ("settled", "completed"), ("completed", "completed"),
    ("posted", "completed"), ("cleared", "completed"), ("captured", "completed"),
    ("refunded", "refunded"), ("reversed", "reversed"), ("voided", "voided"),
    ("declined", "declined"), ("cancelled", "cancelled"), ("canceled", "cancelled"),
    ("delivered", "delivered"), ("in transit", "in_transit"), ("dispatched", "in_transit"),
    ("returned to sender", "returned"),
]

_INSTRUMENT = [
    (r"\bcredit card\b", "credit_card"), (r"\bdebit card\b", "debit_card"),
    (r"\bvisa\b|\bmastercard\b|\bamex\b|\brupay\b", "card"),
    (r"\bupi\b|\bnetbanking\b|\bbank transfer\b|\bach\b|\bsepa\b", "bank_transfer"),
    (r"\bpaypal\b|\bwallet\b", "wallet"),
]


@dataclass
class FactCandidate:
    predicate: str
    value_text: str | None = None
    value_num: float | None = None
    value_norm: str | None = None
    unit: str | None = None
    confidence: float = 0.5
    method: str = "deterministic"
    locator: str = ""
    excerpt: str = ""
    subject_ref: str | None = None
    decision: str = ""            # filled by the trust gate
    reason: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _cap(conf: float, method: str, trust: str | None) -> float:
    c = min(float(conf), METHOD_CEILING.get(method, 0.7))
    return round(c * TRUST_WEIGHT.get(trust or "user_capture", 0.7), 3)


def _line_of(text: str, offset: int) -> tuple[int, str]:
    """(1-indexed line number, the line) for a character offset."""
    before = text[:offset]
    n = before.count("\n") + 1
    start = before.rfind("\n") + 1
    end = text.find("\n", offset)
    return n, text[start: end if end != -1 else len(text)].strip()


# ─────────────────────────────────────────────────────────────────────────────
# deterministic extraction
# ─────────────────────────────────────────────────────────────────────────────
def extract_deterministic(text: str, kind: str) -> list[FactCandidate]:
    """Everything a regular expression can read reliably from this artefact."""
    trust = (EVIDENCE_KINDS.get(kind) or {}).get("trust")
    out: list[FactCandidate] = []
    text = text or ""

    money_pred = MONEY_PREDICATE.get(kind, "stated.amount")
    for amt in normalize.all_money(text):
        ln, line = _line_of(text, amt.get("offset", 0))
        # A labelled total is the artefact's headline figure; an unlabelled number
        # in the body is a line item. Reporting both under the same predicate is
        # what makes a subtotal look like a contradiction against a total.
        labelled = re.search(r"(total|amount|paid|charged|grand total|balance due)",
                             line, re.I)
        pred = money_pred if labelled else money_pred.replace(".total", ".line_item")
        out.append(FactCandidate(
            predicate=pred,
            value_text=normalize.fmt_money(amt["minor"], amt["currency"]),
            value_num=float(amt["minor"]),
            value_norm=f'{amt["minor"]}:{amt["currency"] or "?"}',
            unit=amt["currency"],
            confidence=_cap(0.93 if amt["currency"] else 0.7, "deterministic", trust),
            locator=f"line {ln}", excerpt=line[:180]))

    d = normalize.date(text)
    if d:
        idx = text.find(d["text"])
        ln, line = _line_of(text, max(idx, 0))
        out.append(FactCandidate(
            predicate=DATE_PREDICATE.get(kind, "event.date"),
            value_text=d["iso"], value_norm=d["iso"], unit="date",
            confidence=_cap(0.6 if d["ambiguous"] else 0.92, "deterministic", trust),
            locator=f"line {ln}", excerpt=line[:180],
            reason="ambiguous_date_format" if d["ambiguous"] else ""))

    for ref in normalize.references(text):
        idx = text.find(ref["text"])
        ln, line = _line_of(text, max(idx, 0))
        out.append(FactCandidate(
            predicate=REF_PREDICATE.get(ref["kind"], f'{ref["kind"]}.reference'),
            value_text=ref["value"], value_norm=normalize.canon(ref["value"]),
            confidence=_cap(0.94, "deterministic", trust),
            locator=f"line {ln}", excerpt=line[:180]))

    low = text.lower()
    for word, status in _STATUS_WORDS:
        idx = low.find(word)
        if idx >= 0:
            ln, line = _line_of(text, idx)
            pred = "shipment.status" if status in ("delivered", "in_transit", "returned") \
                else ("booking.status" if kind in ("booking_confirmation", "cancellation_notice")
                      else "charge.status")
            out.append(FactCandidate(
                predicate=pred, value_text=status, value_norm=status,
                confidence=_cap(0.8, "deterministic", trust),
                locator=f"line {ln}", excerpt=line[:180]))
            break

    for pat, inst in _INSTRUMENT:
        m = re.search(pat, low)
        if m:
            ln, line = _line_of(text, m.start())
            out.append(FactCandidate(
                predicate="charge.instrument", value_text=inst, value_norm=inst,
                confidence=_cap(0.88, "deterministic", trust),
                locator=f"line {ln}", excerpt=line[:180]))
            break

    last4 = normalize.card_last4(text)
    if last4:
        out.append(FactCandidate(
            predicate="card.last4", value_text=last4, value_norm=last4,
            confidence=_cap(0.92, "deterministic", trust), locator="card line"))

    fl = normalize.flight_number(text)
    if fl:
        out.append(FactCandidate(
            predicate="flight.number", value_text=fl, value_norm=fl,
            confidence=_cap(0.85, "deterministic", trust), locator="flight line"))

    m = re.search(r"\bdistance\W{0,4}(\d{2,5})\s*(?:km|kilometre|kilometer)", low) \
        or re.search(r"\b(\d{3,5})\s*km\b", low)
    if m:
        # The distance band is what decides an air-passenger compensation amount.
        # Nothing else in the case can supply it, and without it the entitlement
        # calculator returns "unknown" and the claim silently loses its value.
        out.append(FactCandidate(
            predicate="flight.distance_km", value_text=m.group(1),
            value_num=float(m.group(1)), value_norm=m.group(1), unit="km",
            confidence=_cap(0.88, "deterministic", trust),
            locator="distance line", excerpt=m.group(0)))

    m = re.search(r"\b(?:delay(?:ed)?(?:\s+by)?)\s+(\d{1,2})\s*(?:h|hr|hrs|hours?)\b", low)
    if m:
        out.append(FactCandidate(
            predicate="flight.delay_minutes", value_text=m.group(1) + "h",
            value_num=float(m.group(1)) * 60, value_norm=str(int(m.group(1)) * 60),
            unit="minutes", confidence=_cap(0.85, "deterministic", trust),
            locator="delay line", excerpt=m.group(0)))

    return out


# ─────────────────────────────────────────────────────────────────────────────
# model-assisted extraction
# ─────────────────────────────────────────────────────────────────────────────
_LLM_SYSTEM = (
    "You read one consumer document and extract facts. Return ONLY JSON: "
    "{\"facts\": [{\"predicate\": \"...\", \"value\": \"...\", \"confidence\": 0..1, "
    "\"excerpt\": \"the exact substring you read it from\"}]}. "
    "Use dotted predicates such as merchant.name, order.id, order.item, "
    "invoice.total, invoice.period, charge.date, charge.status, booking.reference, "
    "booking.checkin, cancellation.reason, refund.promised_at, plan.allowance, "
    "fee.items, damage.description. "
    "Rules: extract ONLY what the document states. Never infer, never compute, "
    "never fill a value from general knowledge. `excerpt` must appear verbatim in "
    "the document — if you cannot quote it, do not emit the fact."
)


def extract_llm(text: str, kind: str, want: tuple[str, ...] = ()) -> list[FactCandidate]:
    """Model-read facts for the predicates the deterministic pass could not fill.

    The verbatim-excerpt requirement is enforced, not requested: a fact whose
    excerpt is not actually in the document is dropped. That single check removes
    the most damaging class of extraction hallucination — a confidently invented
    amount — because an invented value cannot be quoted from a source.
    """
    trust = (EVIDENCE_KINDS.get(kind) or {}).get("trust")
    hint = f"\n\nFacts still needed: {', '.join(want)}" if want else ""
    try:
        from llm import client
        got = client.chat_json(_LLM_SYSTEM, f"Document type: {kind}\n\n{text[:6000]}{hint}",
                               task="extract")
    except Exception:
        return []
    rows = got.get("facts") if isinstance(got, dict) else None
    if not isinstance(rows, list):
        return []

    low = (text or "").lower()
    out: list[FactCandidate] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        pred, val = r.get("predicate"), r.get("value")
        if not pred or val in (None, ""):
            continue
        excerpt = str(r.get("excerpt") or "")
        if not excerpt or normalize.canon(excerpt)[:60] not in normalize.canon(text)[:20000]:
            continue                     # unquotable: treat as not extracted at all
        try:
            conf = float(r.get("confidence", 0.6))
        except (TypeError, ValueError):
            conf = 0.6
        idx = low.find(excerpt.lower()[:40])
        ln, _ = _line_of(text, max(idx, 0))
        num = None
        mv = normalize.money(str(val))
        if mv:
            num = float(mv["minor"])
        out.append(FactCandidate(
            predicate=str(pred), value_text=str(val),
            value_num=num,
            value_norm=(f'{mv["minor"]}:{mv["currency"] or "?"}' if mv
                        else normalize.canon(val)),
            unit=(mv or {}).get("currency"),
            confidence=_cap(conf, "llm", trust), method="llm",
            locator=f"line {ln}", excerpt=excerpt[:180]))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# the combined pass
# ─────────────────────────────────────────────────────────────────────────────
def extract(text: str, kind: str, *, want: tuple[str, ...] = (),
            use_llm: bool = True) -> list[FactCandidate]:
    """Deterministic first, model second, gate last.

    The gate is `core.trust.gate` unchanged — the same per-field-type thresholds,
    the same absent-is-not-low rule, the same declared policy that ships in the
    audit trail. A consumer case and a compliance invoice are held to one standard
    because they are one product.
    """
    facts = extract_deterministic(text, kind)
    have = {f.predicate for f in facts}

    if use_llm:
        missing = tuple(p for p in want if p not in have)
        if missing or not facts:
            for f in extract_llm(text, kind, missing):
                if f.predicate in have:
                    continue          # deterministic wins; the model never overrides
                facts.append(f)
                have.add(f.predicate)

    for f in facts:
        d = gate.route(f.predicate.rsplit(".", 1)[-1], f.confidence)
        f.decision = d.decision
        if not f.reason:
            f.reason = d.reason
    return facts


def gate_summary(facts: list[FactCandidate]) -> dict:
    """The routing summary for the audit chain — same shape the document pipeline
    writes, so one query answers "what needed a human" across both."""
    return gate.summarise([{**f.as_dict(), "decision": f.decision, "reason": f.reason}
                           for f in facts])
