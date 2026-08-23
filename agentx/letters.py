"""
Communication generation — letters that can only say what the evidence says.

The obvious way to write a dispute letter is to hand a model the case and ask. It
produces excellent prose containing an amount nobody extracted, a date nobody
established, and a statute that does not apply — and the recipient is a disputes
team whose entire job is finding exactly that.

So generation here is inverted. The letter is COMPOSED from established claims and
applicable policies, deterministically. A model may then rewrite it for tone, and
the rewrite is subjected to a grounding check: every money amount, every date and
every reference in the final text must appear in the case's fact graph. A rewrite
that introduces an unsupported figure is discarded and the deterministic draft is
sent instead.

That check is the whole contribution of this module. It converts "the model was
told not to hallucinate" into "an ungrounded letter cannot leave the building",
and the difference is testable — `tests/test_agentx_letters.py` asserts it against
a model that deliberately lies.
"""
from __future__ import annotations

import re

from agentx import eligibility, knowledge, normalize
from agentx import case as case_mod
from agentx.evidence import graph as egraph
from agentx.ontology import REMEDY_KINDS, get as get_definition

# Anything shaped like money, a date, or a reference in the final text must trace
# to a fact. Deliberately broad: a false positive costs a rewrite, a false negative
# costs the user's credibility with a disputes team.
_MONEY = re.compile(r"[₹$£€¥]\s?\d[\d,]*(?:\.\d{1,2})?|\b\d[\d,]*\.\d{2}\b")
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_REF = re.compile(r"\b[A-Z]{2,}[-–]?\d{3,}\b|\b[A-Z0-9]{6,}\b")

_SALUTATION = {
    "merchant_refund": "Request for refund",
    "partial_refund": "Request for a partial refund",
    "bill_correction": "Request for a corrected bill",
    "cancellation": "Notice of cancellation",
    "replacement": "Request for replacement",
    "repair": "Request for repair under guarantee",
    "statutory_compensation": "Claim for statutory compensation",
    "payment_dispute": "Notice of disputed transaction",
    "regulator_complaint": "Formal complaint",
    "insurance_claim": "Claim review request",
    "goodwill_credit": "Request for account credit",
    "explanation": "Request for an itemised explanation",
    "rebooking": "Request for alternative arrangements",
}


def compose(conn, case: dict, params: dict) -> tuple[str, str]:
    """Return (body, subject) for this case's outbound message."""
    remedy = params.get("remedy") or "merchant_refund"
    counterparty = params.get("counterparty") or "Customer Resolution Team"
    definition = get_definition(case.get("problem_type") or "")

    claims = _grounded_claims(conn, case)
    policies = [p for p in eligibility.load_policies(conn, case["id"])
                if p["applies"] == "yes"]
    refs = _references(conn, case["id"])
    amount = normalize.fmt_money(params.get("amount_minor") or case.get("amount_minor"),
                                 params.get("currency") or case.get("currency"))

    subject = (f"{_SALUTATION.get(remedy, 'Request')}"
               + (f" — {refs[0]['value']}" if refs else "")
               + (f" — {case['id']}"))

    body = _deterministic(case, definition, remedy, counterparty, claims, policies,
                          refs, amount)

    polished = _polish(body, case, remedy, counterparty)
    if (polished and _grounded(polished, conn, case["id"], refs, amount)
            and _rules_grounded(polished, conn, case["id"])):
        return polished, subject
    return body, subject


# ─────────────────────────────────────────────────────────────────────────────
# the deterministic draft
# ─────────────────────────────────────────────────────────────────────────────
def _deterministic(case: dict, definition, remedy: str, counterparty: str,
                   claims: list, policies: list[dict], refs: list[dict],
                   amount: str) -> str:
    label = REMEDY_KINDS.get(remedy, {}).get("label", "a resolution")
    lines: list[str] = []
    lines.append(f"To: {counterparty}")
    if refs:
        lines.append("Reference: " + ", ".join(f"{r['label']} {r['value']}" for r in refs[:3]))
    lines.append(f"Our case reference: {case['id']}")
    lines.append("")
    lines.append("Dear Sir or Madam,")
    lines.append("")

    problem = (definition.label if definition else "an issue").lower()
    lines.append(f"I am writing about {problem} on the account referenced above.")
    lines.append("")

    if claims:
        lines.append("What the records show:")
        for cl in claims:
            lines.append(f"  · {cl.text} (confidence {cl.confidence:.2f}, "
                         f"{len(cl.evidence_ids)} document(s) attached)")
        lines.append("")

    if policies:
        lines.append("The basis for this request:")
        for p in policies[:3]:
            lines.append(f"  · {p['title']} — {p['citation']}")
            if p.get("because"):
                lines.append(f"    {_one_line(p['because'])}")
        lines.append("")

    lines.append("What I am asking for:")
    ask = {
        "merchant_refund": f"a full refund of {amount} to the original payment method",
        "partial_refund": f"a refund of the disputed portion of {amount}",
        "bill_correction": "a corrected bill, itemised",
        "cancellation": "cancellation of this service with written confirmation",
        "replacement": "a replacement of the item, or a full refund if none is available",
        "repair": "a repair under the guarantee, or a replacement if repair is not possible",
        "statutory_compensation": f"the statutory compensation due, {amount}",
        "goodwill_credit": "a credit to the account",
        "explanation": "an itemised explanation of each charge on this bill",
        "rebooking": "an equivalent alternative at no additional cost",
        "payment_dispute": f"reversal of the disputed amount of {amount}",
        "regulator_complaint": "a final response so this can be referred onward",
        "insurance_claim": "a review of the decision on this claim",
    }.get(remedy, f"{label.lower()}")
    lines.append(f"  {ask}.")
    lines.append("")
    lines.append("Please confirm in writing what you intend to do, and by when. "
                 "I have kept a full record of this correspondence.")
    lines.append("")
    lines.append("Yours faithfully,")
    lines.append("The account holder")
    lines.append("")
    lines.append(f"— Prepared by Agent X on behalf of the account holder. "
                 f"Case {case['id']}. Every statement above is traceable to an "
                 f"attached document.")
    return "\n".join(lines)


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:220]


def _grounded_claims(conn, case: dict) -> list:
    """Only claims that are actually supported, and not contested.

    A contested claim is deliberately excluded from an outbound letter. Sending a
    figure two of your own documents disagree about hands the recipient the
    argument for free.
    """
    from agentx.engine import _claims
    return [c for c in _claims(conn, case["id"], case)
            if c.confidence >= 0.5 and not c.contested]


def _references(conn, case_id: str) -> list[dict]:
    labels = {"order": "Order", "booking": "Booking", "shipment": "Tracking",
              "policy_number": "Policy", "account": "Account", "case_ref": "Your ref"}
    out = []
    for e in case_mod.entities(conn, case_id):
        if e["kind"] in labels and (e["confidence"] or 0) >= 0.6:
            out.append({"label": labels[e["kind"]], "value": e["value"]})
    return out


# ─────────────────────────────────────────────────────────────────────────────
# the model rewrite, and the check that governs it
# ─────────────────────────────────────────────────────────────────────────────
_POLISH_SYSTEM = (
    "You rewrite a consumer complaint letter for tone and clarity. Return ONLY the "
    "rewritten letter as plain text, no preamble, no markdown. "
    "HARD RULES: do not add any amount, date, reference number, statute, or fact "
    "that is not already in the draft. Do not remove the reference lines or the "
    "closing attribution. Keep it under 300 words. Be firm, specific and polite; "
    "no threats, no legal advice, no invented deadlines."
)


def _polish(draft: str, case: dict, remedy: str, counterparty: str) -> str | None:
    try:
        from llm import client
        out = client.chat(_POLISH_SYSTEM,
                          f"Rewrite this letter:\n\n{draft}", temperature=0.2,
                          max_tokens=700).strip()
    except Exception:
        return None
    if len(out) < 120 or len(out) > 4000:
        return None
    return out


def _grounded(text: str, conn, case_id: str, refs: list[dict],
              amount: str) -> bool:
    """Does every figure in this text trace to the case's evidence?

    Money and dates are checked against the fact graph's normalised values;
    references against the case's entities. Anything the model introduced that
    Agent X cannot point at is a failure, and the caller falls back to the
    deterministic draft.
    """
    facts = egraph.facts_for(conn, case_id)
    known_money: set[str] = set()
    known_dates: set[str] = set()
    for f in facts:
        if f["value_num"] is not None and f["unit"]:
            known_money.add(normalize.fmt_money(int(f["value_num"]), f["unit"]))
            known_money.add(f"{int(f['value_num']) / 100:,.2f}")
            known_money.add(f"{int(f['value_num']) / 100:.2f}")
        if f["unit"] == "date" and f["value_norm"]:
            known_dates.add(f["value_norm"])
        if f["value_text"]:
            known_money.add(str(f["value_text"]))
    if amount and amount != "—":
        known_money.add(amount)

    for m in _MONEY.findall(text):
        token = m.strip()
        bare = re.sub(r"[^\d.,]", "", token).replace(",", "")
        if token in known_money:
            continue
        if any(bare and bare in re.sub(r"[^\d.,]", "", k).replace(",", "")
               for k in known_money):
            continue
        return False

    for d in _DATE.findall(text):
        if d not in known_dates:
            return False

    known_refs = {r["value"].upper() for r in refs}
    known_refs |= {str(e["value"]).upper() for e in case_mod.entities(conn, case_id)}
    known_refs.add(case_id.upper())
    for r in _REF.findall(text):
        token = r.upper()
        if token in known_refs or any(token in k or k in token for k in known_refs):
            continue
        # An all-caps word is not a reference; only tokens containing a digit are.
        if not any(ch.isdigit() for ch in token):
            continue
        return False

    return True


# ─────────────────────────────────────────────────────────────────────────────
# rule grounding — a letter may only cite law the case actually established
# ─────────────────────────────────────────────────────────────────────────────
# `_grounded` above checks amounts, dates and references. It does not look at
# legal claims, and that was a real hole: a rewrite that keeps every figure
# honest and adds "under Section 75 of the Consumer Credit Act 1974 you must
# refund within 14 working days" passed, and went out. Nothing in the case
# established either statute. A disputes team's first move is to check the rule
# you cited, and citing one that does not apply loses an otherwise winnable case
# — which makes a fabricated citation more damaging than a fabricated figure,
# not less.
#
# Anything shaped like a rule is caught deliberately broadly. A false positive
# costs a rewrite; a false negative sends an invented statute to a counterparty.
_RULE_PATTERNS = (
    # "Section 75", "Article 7(1)", "Regulation 14", "Rule 12", "clause 4.2"
    re.compile(r"\b(?:section|article|regulation|reg\.|rule|clause|paragraph|para\.?)"
               r"\s*\d+[\w.()\-]*", re.I),
    # "Consumer Credit Act 1974", "Payment Services Regulations 2017"
    re.compile(r"\b(?:[A-Z][\w’'()-]*\s+){1,6}"
               r"(?:Act|Regulations|Directive|Convention|Rules|Scheme|Code)"
               r"(?:,?\s+\d{4})?"),
    # "15 U.S.C. § 1666", "EU261", "Regulation (EC) No 261/2004"
    re.compile(r"\b\d+\s*U\.?S\.?C\.?\s*(?:§\s*)?\d+", re.I),
    re.compile(r"\bEU\s?\d{3,}\b", re.I),
    re.compile(r"\bNo\.?\s?\d{3}/\d{4}\b"),
)


def _rule_mentions(text: str) -> list[str]:
    """Every legal-rule-shaped phrase in a letter, de-duplicated in order."""
    found: list[str] = []
    for pattern in _RULE_PATTERNS:
        for match in pattern.findall(text):
            phrase = " ".join(str(match).split()).strip(" .,;:")
            if phrase and phrase.lower() not in {f.lower() for f in found}:
                found.append(phrase)
    return found


def _established_rules(conn, case_id: str) -> str:
    """The text of every rule this case actually established, as one corpus.

    Drawn from the applicable policy findings — the deterministic corpus, which
    is the only thing entitled to establish an entitlement — plus any research
    citation that verified against its source. Research that came back partial,
    unsupported or conflicting contributes nothing here, which is the point.
    """
    parts: list[str] = []
    for p in eligibility.load_policies(conn, case_id):
        if p["applies"] != "yes":
            continue
        parts += [p.get("citation") or "", p.get("title") or "",
                  p.get("authority") or ""]
    try:
        from agentx import research
        for row in research.load(conn, case_id)["citations"]:
            if row.get("verdict") == "verified" and row.get("claim"):
                parts.append(row["claim"])
    except Exception:
        # Research is an enhancement; a case whose research table is unavailable
        # still gets the policy-corpus check, which is the load-bearing half.
        pass
    return "\n".join(p for p in parts if p)


def _rules_grounded(text: str, conn, case_id: str) -> bool:
    """Does every rule this letter cites trace to one the case established?"""
    mentions = _rule_mentions(text)
    if not mentions:
        return True
    established = _established_rules(conn, case_id)
    if not established.strip():
        # The letter cites law and the case established none. There is no
        # charitable reading of that.
        return False
    source = [{"id": f"case:{case_id}", "text": established}]
    return all(knowledge.verify_citation(m, source).safe_to_state
               for m in mentions)


def grounding_report(text: str, conn, case_id: str) -> dict:
    """Why a letter passed or failed grounding — for tests and for the UI."""
    refs = _references(conn, case_id)
    ok = _grounded(text, conn, case_id, refs, "")
    mentions = _rule_mentions(text)
    established = _established_rules(conn, case_id)
    source = [{"id": f"case:{case_id}", "text": established}] if established.strip() else []
    checks = [knowledge.verify_citation(m, source) for m in mentions]
    return {"grounded": ok and all(c.safe_to_state for c in checks),
            "figures_grounded": ok,
            "rules_grounded": all(c.safe_to_state for c in checks),
            "money_tokens": _MONEY.findall(text),
            "date_tokens": _DATE.findall(text),
            "reference_tokens": [r for r in _REF.findall(text)
                                 if any(ch.isdigit() for ch in r)],
            "rule_citations": [c.as_dict() for c in checks],
            "rule": "every amount, date and reference in an outbound letter must "
                    "appear in the case's fact graph, and every rule it cites "
                    "must be one the case established"}
