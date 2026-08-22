"""
Policy and rights analysis — deterministic, cited, and honest about what it does
not know.

This is the layer between "here is what happened" and "here is what you are owed",
and it is the layer most consumer AI gets wrong. The failure mode is not that a
model cites the wrong statute; it is that it produces a confident sentence about
entitlement from a prompt, so the sentence is equally fluent whether the arrival
delay was 175 minutes or 185 — which is the entire question.

So the split here is strict:

    the CORPUS      declares rights, windows and conditions as data (policies.yaml)
    the EVALUATOR   applies conditions to the case fact graph, deterministically
    the LLM         may only phrase the outcome for a human; it never decides one

Three outcomes, never two. A policy `applies`, does `not apply`, or is `unknown` —
and unknown is a first-class result, produced whenever a condition depends on a
fact the case does not yet have. That unknown then flows straight into the
question queue, which is how Agent X converts a legal gap into a single question to
the user rather than an assumption.

Jurisdiction is likewise never assumed. Consumer law is territorial; the same
facts produce different entitlements in London, Delhi and Denver. Where Agent X
cannot establish which regime governs, every jurisdiction-specific policy is
reported as unknown with jurisdiction named as the reason.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

import yaml

from agentx.ontology import ProblemDefinition

CORPUS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "policies.yaml")

# Currency is the cheapest jurisdiction signal available and the one most often
# present on the evidence itself. It is a HINT, not a determination — a euro
# charge from a UK merchant is common — so it only ever produces a candidate.
CURRENCY_JURISDICTION = {
    "GBP": "UK", "EUR": "EU", "USD": "US", "INR": "IN",
    "CAD": "CA", "AUD": "AU", "SGD": "SG", "AED": "AE",
}

# Policies that apply wherever the transaction happened.
UNIVERSAL = ("global", "contract")


@dataclass(frozen=True)
class Policy:
    id: str
    title: str
    authority: str
    jurisdiction: str
    citation: str
    summary: str
    grants: tuple[str, ...] = ()
    window_days: int | None = None
    window_from: str = "incident"
    conditions: tuple[dict, ...] = ()
    entitlement: str | None = None
    dynamic: bool = False
    notes: str = ""


@dataclass
class PolicyFinding:
    """One policy, evaluated against one case."""
    policy: Policy
    applies: str                       # yes | no | unknown
    because: str
    unmet: list[str] = field(default_factory=list)
    unknown_facts: list[str] = field(default_factory=list)
    entitlement_minor: int | None = None
    entitlement_currency: str | None = None
    deadline_days: int | None = None

    def as_row(self) -> dict:
        return {
            "policy_id": self.policy.id,
            "title": self.policy.title,
            "authority": self.policy.authority,
            "jurisdiction": self.policy.jurisdiction,
            "applies": self.applies,
            "because": self.because,
            "citation": self.policy.citation,
            "window_days": self.policy.window_days,
        }


# ─────────────────────────────────────────────────────────────────────────────
# corpus
# ─────────────────────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def corpus() -> dict[str, Policy]:
    with open(CORPUS_PATH, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    out: dict[str, Policy] = {}
    for p in raw.get("policies") or []:
        out[p["id"]] = Policy(
            id=p["id"], title=p.get("title", p["id"]),
            authority=p.get("authority", "unknown"),
            jurisdiction=p.get("jurisdiction", "unknown"),
            citation=p.get("citation", ""), summary=(p.get("summary") or "").strip(),
            grants=tuple(p.get("grants") or ()),
            window_days=p.get("window_days"),
            window_from=p.get("window_from", "incident"),
            conditions=tuple(p.get("conditions") or ()),
            entitlement=p.get("entitlement"),
            dynamic=bool(p.get("dynamic")),
            notes=(p.get("notes") or "").strip())
    return out


def reload() -> dict[str, Policy]:
    corpus.cache_clear()
    return corpus()


def missing_references() -> dict[str, list[str]]:
    """Policy ids cited by a problem definition that the corpus does not define.

    Exercised by the test suite. A dangling citation is not cosmetic: it is a
    right Agent X will silently never assert, and silence is the failure that costs
    a user money.
    """
    from agentx.ontology import catalogue
    known = set(corpus())
    out: dict[str, list[str]] = {}
    for d in catalogue().values():
        missing = [p for p in d.policies if p not in known]
        if missing:
            out[d.problem_type] = missing
    return out


# ─────────────────────────────────────────────────────────────────────────────
# jurisdiction
# ─────────────────────────────────────────────────────────────────────────────
def detect_jurisdiction(*, currency: str | None = None, stated: str | None = None,
                        merchant_country: str | None = None) -> tuple[str | None, str]:
    """(jurisdiction, why). None when it genuinely cannot be established."""
    if stated:
        return stated.upper(), "the user told us"
    if merchant_country:
        return merchant_country.upper(), "the merchant's country of establishment"
    if currency and currency.upper() in CURRENCY_JURISDICTION:
        j = CURRENCY_JURISDICTION[currency.upper()]
        return j, f"inferred from the {currency.upper()} currency on the evidence"
    return None, "no jurisdiction signal in the case yet"


# ─────────────────────────────────────────────────────────────────────────────
# condition evaluation
# ─────────────────────────────────────────────────────────────────────────────
def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _check(cond: dict, facts: dict) -> tuple[str, str]:
    """Evaluate one condition. Returns (yes|no|unknown, explanation)."""
    key = cond.get("fact", "")
    op = cond.get("op", "exists")
    want = cond.get("value")
    have = facts.get(key)

    if have is None:
        if cond.get("skip_if_absent"):
            return "unknown", f"{key} is not established yet"
        return "unknown", f"{key} is not established yet"

    if op == "exists":
        return "yes", f"{key} is present"
    if op in (">=", "<=", ">", "<"):
        a, b = _num(have), _num(want)
        if a is None or b is None:
            return "unknown", f"{key} is not a number ({have!r})"
        ok = {">=": a >= b, "<=": a <= b, ">": a > b, "<": a < b}[op]
        return ("yes" if ok else "no"), f"{key} = {have} {op} {want}"
    if op == "between":
        a = _num(have)
        lo, hi = (_num(want[0]), _num(want[1])) if isinstance(want, (list, tuple)) else (None, None)
        if a is None or lo is None or hi is None:
            return "unknown", f"{key} cannot be compared to a range"
        return ("yes" if lo <= a <= hi else "no"), f"{key} = {have}, range {lo}–{hi}"
    if op in ("in", "not_in"):
        pool = [str(x).lower() for x in (want or [])]
        hit = str(have).lower() in pool
        ok = hit if op == "in" else not hit
        return ("yes" if ok else "no"), f"{key} = {have!r}"
    if op == "==":
        return ("yes" if str(have).lower() == str(want).lower() else "no"), f"{key} = {have!r}"
    return "unknown", f"unsupported operator {op!r}"


# ─────────────────────────────────────────────────────────────────────────────
# entitlement calculators
# ─────────────────────────────────────────────────────────────────────────────
def _band(distance_km) -> str | None:
    d = _num(distance_km)
    if d is None:
        return None
    if d <= 1500:
        return "short"
    if d <= 3500:
        return "medium"
    return "long"


def eu261_amount(facts: dict) -> tuple[int | None, str]:
    """EUR compensation in minor units by distance band, per Article 7."""
    band = _band(facts.get("flight.distance_km"))
    table = {"short": 25000, "medium": 40000, "long": 60000}
    if band is None:
        return None, "flight distance is not established, so the EU261 band is unknown"
    return table[band], f"Article 7 {band}-haul band"


def uk261_amount(facts: dict) -> tuple[int | None, str]:
    band = _band(facts.get("flight.distance_km"))
    table = {"short": 22000, "medium": 35000, "long": 52000}
    if band is None:
        return None, "flight distance is not established, so the UK261 band is unknown"
    return table[band], f"UK261 {band}-haul band"


ENTITLEMENTS = {"eu261_amount": (eu261_amount, "EUR"),
                "uk261_amount": (uk261_amount, "GBP")}


# ─────────────────────────────────────────────────────────────────────────────
# the public entry point
# ─────────────────────────────────────────────────────────────────────────────
def analyse(definition: ProblemDefinition, facts: dict,
            jurisdiction: str | None = None) -> list[PolicyFinding]:
    """Evaluate every policy this problem type cites, against this case's facts.

    Returns one finding per cited policy, including the ones that do NOT apply.
    Keeping the negatives is deliberate: "you are outside the 120-day chargeback
    window" is one of the most valuable things Agent X can tell a user, and a system
    that only reports hits cannot say it.
    """
    cat = corpus()
    findings: list[PolicyFinding] = []
    for pid in definition.policies:
        pol = cat.get(pid)
        if pol is None:
            continue

        # jurisdiction gate first: a policy from the wrong regime is not "unmet",
        # it is simply not the law here, and saying so is clearer than a list of
        # conditions the user could never satisfy.
        if pol.jurisdiction not in UNIVERSAL:
            if jurisdiction is None:
                findings.append(PolicyFinding(
                    pol, "unknown",
                    f"{pol.title} is {pol.jurisdiction} law and Agent X has not "
                    f"established which country's rules govern this purchase.",
                    unknown_facts=["case.jurisdiction"]))
                continue
            if jurisdiction != pol.jurisdiction and not (
                    pol.jurisdiction == "EU" and jurisdiction in ("EU", "IE", "DE", "FR", "ES", "IT", "NL")):
                findings.append(PolicyFinding(
                    pol, "no",
                    f"{pol.title} governs {pol.jurisdiction} transactions; this one "
                    f"is {jurisdiction}."))
                continue

        verdicts: list[tuple[str, str, dict]] = []
        for cond in pol.conditions:
            v, why = _check(cond, facts)
            verdicts.append((v, why, cond))

        failed = [(w, c) for v, w, c in verdicts if v == "no"]
        unknown = [(w, c) for v, w, c in verdicts if v == "unknown"]

        if failed:
            reason = failed[0][1].get("else") or f"condition not met: {failed[0][0]}"
            findings.append(PolicyFinding(pol, "no", reason,
                                          unmet=[w for w, _ in failed]))
            continue
        if unknown:
            findings.append(PolicyFinding(
                pol, "unknown",
                f"{pol.title} may apply, but {unknown[0][0]}.",
                unknown_facts=[c.get("fact", "") for _, c in unknown]))
            continue

        amount, note = (None, "")
        currency = None
        if pol.entitlement and pol.entitlement in ENTITLEMENTS:
            calc, currency = ENTITLEMENTS[pol.entitlement]
            amount, note = calc(facts)
        because = pol.summary.strip() or pol.title
        if note:
            because = f"{because} ({note})"
        findings.append(PolicyFinding(pol, "yes", because,
                                      entitlement_minor=amount,
                                      entitlement_currency=currency if amount else None,
                                      deadline_days=pol.window_days))
    return findings


def granted_remedies(findings: list[PolicyFinding]) -> dict[str, list[str]]:
    """remedy kind -> the policies that support it. Only from applicable findings.

    An `unknown` policy grants nothing yet. Letting maybes grant remedies is how
    an agent ends up filing a chargeback under a right it never actually
    established, which is exactly the class of action that cannot be taken back.
    """
    out: dict[str, list[str]] = {}
    for f in findings:
        if f.applies != "yes":
            continue
        for r in f.policy.grants:
            out.setdefault(r, []).append(f.policy.id)
    return out


def blocking_unknowns(findings: list[PolicyFinding]) -> list[str]:
    """Facts that, if known, would resolve an `unknown` policy into a decision."""
    seen: list[str] = []
    for f in findings:
        for k in f.unknown_facts:
            if k and k not in seen:
                seen.append(k)
    return seen
