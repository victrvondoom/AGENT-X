"""
Problem understanding — what happened, held as a distribution rather than a label.

A consumer classifier that returns one label is making a bet it cannot justify.
"They charged me again" is consistent with a duplicate, a renewal, a
pre-authorisation hold, an instalment, a corrected re-issue and outright fraud, and
those six have different evidence requirements, different remedies and wildly
different risk. Picking one and proceeding is how an agent files a chargeback
against a subscription the user simply forgot about.

So understanding here produces a POSTERIOR OVER HYPOTHESES, and stays plural until
something separates them. Three mechanisms do the separating, in this order:

  1. lexical evidence from the declarative catalogue — phrases, patterns, negative
     phrases, and ambiguity-group triggers that deliberately wake every rival;
  2. the evidence the user actually attached, which reweights toward problem types
     whose requirements it satisfies;
  3. the minimum set of questions, chosen by EXPECTED INFORMATION GAIN over the
     current distribution rather than by asking everything on a checklist.

An LLM may participate, and its role is bounded on purpose: it can move mass
between problem types that already exist in the catalogue and it can propose
entities, but it cannot invent a problem type, cannot set a posterior on its own,
and cannot answer a question the evidence has not answered. Every number it
contributes is fused with the deterministic prior and the fusion is recorded, so a
case's reasoning is reproducible without the model.

The information-gain calculation is the part worth reading. Each discriminator
declares which hypotheses an affirmative answer favours and disfavours; that is a
likelihood model, and a likelihood model is all you need to compute how much a
question is worth before you ask it. Agent X asks the highest-value question first
and stops as soon as one hypothesis clears the margin — which is why a real case
takes one question rather than a form.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, asdict

from agentx import normalize
from agentx.ontology import ProblemDefinition, catalogue, group_trigger_index

# How much a single lexical hit moves the log-odds. Tuned so that one exact phrase
# match dominates a prior difference but two rivals each matching once stay close —
# the point is to separate confident cases, not to manufacture confidence.
PHRASE_WEIGHT = 1.15
PATTERN_WEIGHT = 1.0
NEGATIVE_WEIGHT = -1.4
EVIDENCE_WEIGHT = 0.55
DOMAIN_WEIGHT = 0.35

# A case is ambiguous unless the leader clears this margin over the runner-up.
# Below it Agent X asks rather than acts, and the threshold is declared here rather
# than buried in a branch so it can be argued with.
DECISIVE_MARGIN = 0.22
DECISIVE_FLOOR = 0.45

# Mass reserved for "this is a consumer problem the catalogue does not model".
# Without it a single matching definition produces a posterior of exactly 1.0,
# which is a claim no keyword match can support and which would let the governor
# treat a one-phrase match as certainty. It also gives the UI an honest number for
# how much of the probability space Agent X cannot see.
RESIDUAL_PRIOR = 0.04


@dataclass
class Hypothesis:
    problem_type: str
    domain: str
    label: str
    prior: float
    score: float                    # log-space evidence, pre-normalisation
    posterior: float
    signals: list[str] = field(default_factory=list)
    rationale: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Understanding:
    text: str
    hypotheses: list[Hypothesis]
    entities: list[dict]
    ambiguous: bool
    margin: float
    llm_used: bool = False
    llm_note: str = ""
    residual: float = 0.0

    @property
    def top(self) -> Hypothesis | None:
        return self.hypotheses[0] if self.hypotheses else None

    def live(self, floor: float = 0.05) -> list[Hypothesis]:
        """Hypotheses still worth carrying. Anything under the floor is noise."""
        return [h for h in self.hypotheses if h.posterior >= floor]

    def distribution(self) -> dict[str, float]:
        return {h.problem_type: h.posterior for h in self.hypotheses}

    def as_dict(self) -> dict:
        return {"text": self.text,
                "hypotheses": [h.as_dict() for h in self.hypotheses],
                "entities": self.entities, "ambiguous": self.ambiguous,
                "margin": round(self.margin, 4), "residual": round(self.residual, 4),
                "llm_used": self.llm_used, "llm_note": self.llm_note}


# ─────────────────────────────────────────────────────────────────────────────
# entity extraction
# ─────────────────────────────────────────────────────────────────────────────
# Merchant detection is a lexicon plus a capitalisation heuristic, and it is
# deliberately conservative: a wrong merchant sends a dispute letter to the wrong
# company, which is worse than asking. Unknown merchants surface as a question.
KNOWN_MERCHANTS = {
    "amazon": "Amazon", "flipkart": "Flipkart", "ebay": "eBay", "etsy": "Etsy",
    "netflix": "Netflix", "spotify": "Spotify", "adobe": "Adobe", "apple": "Apple",
    "google": "Google", "microsoft": "Microsoft", "dropbox": "Dropbox",
    "booking.com": "Booking.com", "airbnb": "Airbnb", "expedia": "Expedia",
    "marriott": "Marriott", "hilton": "Hilton", "oyo": "OYO", "agoda": "Agoda",
    "makemytrip": "MakeMyTrip", "uber": "Uber", "ola": "Ola", "lyft": "Lyft",
    "ryanair": "Ryanair", "easyjet": "easyJet", "british airways": "British Airways",
    "indigo": "IndiGo", "air india": "Air India", "emirates": "Emirates",
    "lufthansa": "Lufthansa", "vodafone": "Vodafone", "airtel": "Airtel",
    "jio": "Jio", "bt": "BT", "sky": "Sky", "virgin media": "Virgin Media",
    "octopus energy": "Octopus Energy", "british gas": "British Gas",
    "swiggy": "Swiggy", "zomato": "Zomato", "myntra": "Myntra", "zara": "Zara",
    # The sandbox companies. Present in the same lexicon as the real ones because
    # the resolution path must not differ: a case against Kartly is routed by the
    # same extraction that routes one against Amazon, and only the provider
    # registry knows the difference.
    "skylink": "SkyLink Airways", "skylink airways": "SkyLink Airways",
    "meridian": "Meridian Suites", "meridian suites": "Meridian Suites",
    "kartly": "Kartly", "streamly": "Streamly",
    "nimbus": "Nimbus Mobile", "nimbus mobile": "Nimbus Mobile",
}

_PROPER = re.compile(r"\b([A-Z][a-zA-Z&.'-]{2,}(?:\s+[A-Z][a-zA-Z&.'-]{2,}){0,2})\b")
_SENTENCE_START = re.compile(r"(?:^|[.!?]\s+)([A-Z])")


def extract_entities(text: str) -> list[dict]:
    """Entities from free text, each with a confidence and its own provenance.

    Confidence here is a real signal, not decoration: it feeds the gate that
    decides whether Agent X can act unattended, and a merchant guessed from
    capitalisation must never carry the same weight as one read off a receipt.
    """
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, value: str, conf: float, source: str, norm: str | None = None):
        key = (kind, normalize.canon(value))
        if not value or key in seen:
            return
        seen.add(key)
        out.append({"kind": kind, "value": str(value), "normalized": norm or normalize.canon(value),
                    "confidence": round(conf, 2), "source": source})

    low = (text or "").lower()

    # Whole words, longest first. Substring matching made "SkyLink" resolve to
    # "Sky" — a different company entirely — and the case then had no provider,
    # no terms and no route to a remedy. A merchant misidentification is the most
    # expensive extraction error in the product: everything downstream is
    # addressed to the wrong company.
    for token in sorted(KNOWN_MERCHANTS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(token)}\b", low):
            add("merchant", KNOWN_MERCHANTS[token], 0.92, "narrative:lexicon")
            break
    else:
        # No known merchant. Take at most one capitalised phrase that is not at a
        # sentence start, at low confidence — enough to show the user what Agent X
        # thinks it saw, not enough to act on.
        starts = {m.start(1) for m in _SENTENCE_START.finditer(text or "")}
        for m in _PROPER.finditer(text or ""):
            token = m.group(1)
            if m.start(1) in starts or token.lower() in _STOPWORD_PROPER:
                continue
            # An ALL-CAPS token is an acronym, a currency code or a reference —
            # never a company name in running prose. Guessing "INR" is the
            # merchant sends a dispute letter to a currency.
            if token.isupper() or token.upper() in normalize.CURRENCY_CODES:
                continue
            add("merchant", token, 0.42, "narrative:capitalisation")
            break

    for amt in normalize.all_money(text or ""):
        add("amount", normalize.fmt_money(amt["minor"], amt["currency"]),
            0.85 if amt["currency"] else 0.55, "narrative:amount",
            norm=f'{amt["minor"]}:{amt["currency"] or "?"}')

    d = normalize.date(text or "")
    if d:
        add("date", d["iso"], 0.55 if d["ambiguous"] else 0.85, "narrative:date", norm=d["iso"])

    for ref in normalize.references(text or ""):
        add(ref["kind"], ref["value"], 0.88, "narrative:reference")

    last4 = normalize.card_last4(text or "")
    if last4:
        add("card", f"•••• {last4}", 0.9, "narrative:card")

    fl = normalize.flight_number(text or "")
    if fl:
        add("booking", fl, 0.7, "narrative:flight")

    return out


_STOPWORD_PROPER = {"i", "my", "the", "they", "hotel", "flight", "amazon pay",
                    "order", "booking", "reference", "customer", "dear", "please",
                    "thanks", "regards", "monday", "tuesday", "wednesday",
                    "thursday", "friday", "saturday", "sunday"}


# ─────────────────────────────────────────────────────────────────────────────
# phrase matching
# ─────────────────────────────────────────────────────────────────────────────
# Words that carry no signal about WHICH problem this is. Dropped from a declared
# phrase before matching, so "they cancelled my booking" in the catalogue still
# recognises "Meridian Suites cancelled my booking" in a user's sentence.
_FILLER_TOKENS = {
    "a", "an", "the", "my", "me", "i", "we", "they", "it", "is", "was", "were",
    "to", "of", "for", "and", "that", "this", "have", "has", "had", "do", "did",
    "am", "are", "be", "been", "on", "in", "at", "so", "as", "by", "with",
}


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def _token_match(want: str, got: str) -> bool:
    """Same word, allowing an inflection.

    `cancel` matches `cancelled`, `renew` matches `renewal`. Prefix matching is
    only allowed from four characters up, because below that it over-matches
    ("bill" would swallow "billion") and the catalogue is written in whole words
    anyway.
    """
    if want == got:
        return True
    short, long = (want, got) if len(want) <= len(got) else (got, want)
    return len(short) >= 4 and long.startswith(short)


def _phrase_hit(phrase: str, text_tokens: list[str], low: str) -> float:
    """1.0 for an exact substring, 0.65 for a loose in-order match, 0 otherwise.

    Exact substring matching alone is far too brittle for consumer prose. A user
    writes "renewed my annual subscription without me realising" and a catalogue
    that declares "renewed without" sees nothing — which in practice meant a case
    with an obvious classification fell through to "Agent X does not understand
    this". Loose matching keeps the declared phrases short and readable while
    still recognising the sentence people actually type.

    The looseness is bounded: every content word must appear, in order, inside a
    window proportional to the phrase length. That admits intervening words and
    still refuses to match two words scattered across a paragraph.
    """
    p_low = phrase.lower()
    if p_low in low:
        return 1.0
    want = [t for t in _tokens(p_low) if t not in _FILLER_TOKENS]
    if not want:
        return 0.0
    window = len(want) + 6
    start = 0
    while start < len(text_tokens):
        i, matched, first = start, 0, None
        while i < len(text_tokens) and matched < len(want):
            if _token_match(want[matched], text_tokens[i]):
                if first is None:
                    first = i
                matched += 1
            elif first is not None and (i - first) > window:
                break
            i += 1
        if matched == len(want):
            return 0.65
        if first is None:
            return 0.0
        start = first + 1
    return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# hypothesis scoring
# ─────────────────────────────────────────────────────────────────────────────
def _lexical_score(d: ProblemDefinition, low: str) -> tuple[float, list[str]]:
    score, signals = 0.0, []
    toks = _tokens(low)
    for p in d.phrases:
        hit = _phrase_hit(p, toks, low)
        if hit:
            score += PHRASE_WEIGHT * hit
            signals.append(f'phrase "{p}"' + ("" if hit == 1.0 else " (loose)"))
    for pat in d.patterns:
        try:
            if re.search(pat, low, re.I):
                score += PATTERN_WEIGHT
                signals.append(f"pattern /{pat}/")
        except re.error:
            continue
    for n in d.negative_phrases:
        # Counter-signals are matched EXACTLY. A loose negative would rule out a
        # correct interpretation on a coincidence, and a false negative here is
        # silent: the case simply never considers the right answer.
        if n.lower() in low:
            score += NEGATIVE_WEIGHT
            signals.append(f'counter-signal "{n}"')
    return score, signals


def _evidence_score(d: ProblemDefinition, kinds: tuple[str, ...]) -> tuple[float, list[str]]:
    if not kinds:
        return 0.0, []
    score, signals = 0.0, []
    for req in d.required_evidence:
        for k in kinds:
            if req.accepts(k):
                w = EVIDENCE_WEIGHT * (1.0 if req.critical else 0.5)
                score += w
                signals.append(f"evidence {k} satisfies a required {req.kind}")
                break
    return score, signals


def hypotheses(text: str, evidence_kinds: tuple[str, ...] = (),
               domain_hint: str | None = None) -> list[Hypothesis]:
    """Score every problem type in the catalogue against this narrative.

    Returns the full ranked list, not the winner. Callers decide how much of the
    tail to carry; the engine carries everything above 5%.
    """
    low = (text or "").lower()
    cat = catalogue()

    # Ambiguity-group triggers fire first. A phrase that names the GROUP is
    # evidence for every member equally, so it lifts them all rather than being
    # attributed to whichever member's keyword list mentions it.
    woken: dict[str, str] = {}
    for group, pats in group_trigger_index().items():
        for pat in pats:
            try:
                if re.search(pat, low, re.I):
                    for member in cat.values():
                        if member.ambiguity_group == group:
                            woken[member.problem_type] = f'group trigger /{pat}/ for "{group}"'
                    break
            except re.error:
                continue

    scored: list[Hypothesis] = []
    # Any definition that scores drags its ambiguity group in with it, at prior
    # weight and zero evidence. "Charged me twice" is strong evidence for a
    # duplicate and is still weak evidence against a corrected re-issue, so the
    # rivals have to remain on the table to be ruled out rather than forgotten.
    pulled_in: set[str] = set(woken)
    for d in cat.values():
        lex_probe, _ = _lexical_score(d, low)
        if lex_probe > 0 and d.ambiguity_group:
            pulled_in.update(m.problem_type for m in cat.values()
                             if m.ambiguity_group == d.ambiguity_group)

    for d in cat.values():
        lex, sig = _lexical_score(d, low)
        ev, esig = _evidence_score(d, evidence_kinds)
        sig += esig
        dom = 0.0
        if domain_hint and d.domain == domain_hint:
            dom = DOMAIN_WEIGHT
            sig.append(f"domain hint {domain_hint}")
        if d.problem_type in woken:
            sig.append(woken[d.problem_type])

        # Evidence CORROBORATES a hypothesis; it does not create one. A bank
        # statement satisfies the evidence requirement of a dozen problem types,
        # and letting that alone wake them turns every case with a statement
        # attached into a flat distribution over the whole catalogue — which is
        # what drove confidence below the governor's floor and stalled cases that
        # were, in fact, perfectly clear. The narrative proposes; evidence weighs.
        if lex <= 0 and dom <= 0 and d.problem_type not in pulled_in:
            continue
        raw = lex + ev + dom
        if lex <= 0 and dom <= 0:
            sig.append("carried as a rival interpretation, not yet ruled out")
        scored.append(Hypothesis(
            problem_type=d.problem_type, domain=d.domain, label=d.label,
            prior=d.prior, score=raw, posterior=0.0, signals=sig,
            rationale=d.summary.strip()))

    if not scored:
        return []

    # Posterior ∝ prior · exp(evidence). Log-linear rather than additive so that
    # a prior can be overturned by evidence but never ignored, and so a problem
    # type with no signal at all cannot win on prior alone.
    total = RESIDUAL_PRIOR
    for h in scored:
        h.posterior = h.prior * math.exp(h.score)
        total += h.posterior
    for h in scored:
        h.posterior = h.posterior / total if total else 0.0
    scored.sort(key=lambda h: -h.posterior)
    return scored


def residual_mass(hyps: list[Hypothesis]) -> float:
    """Probability that none of the carried hypotheses is right.

    Surfaced rather than hidden: a case sitting at 40% residual is one Agent X does
    not understand, and the correct response is to say so and ask, not to act on
    the best of a bad field."""
    return max(0.0, 1.0 - sum(h.posterior for h in hyps))


def margin_of(hyps: list[Hypothesis]) -> float:
    if not hyps:
        return 0.0
    if len(hyps) == 1:
        return hyps[0].posterior
    return hyps[0].posterior - hyps[1].posterior


def is_ambiguous(hyps: list[Hypothesis]) -> bool:
    """Ambiguous unless the leader both clears the floor and beats the field.

    Both conditions matter. A leader at 0.44 against a field of 0.05s is confident
    in relative terms and still weak in absolute ones — that is the shape of a case
    where the narrative was too thin to say anything, and acting on it is how an
    agent writes a dispute letter about the wrong transaction.
    """
    if not hyps:
        return True
    return not (hyps[0].posterior >= DECISIVE_FLOOR and margin_of(hyps) >= DECISIVE_MARGIN)


# ─────────────────────────────────────────────────────────────────────────────
# expected information gain
# ─────────────────────────────────────────────────────────────────────────────
def _entropy(dist: dict[str, float]) -> float:
    return -sum(p * math.log2(p) for p in dist.values() if p > 0)


def _normalise(d: dict[str, float]) -> dict[str, float]:
    t = sum(d.values())
    return {k: v / t for k, v in d.items()} if t > 0 else d


def _likelihood_affirmative(disc, problem_type: str) -> float:
    """P(user answers the affirmative option | this hypothesis is true).

    Derived from the declared favours/disfavours weights: a discriminator saying
    it favours `duplicate_charge` by 0.35 is saying a duplicate makes the
    affirmative answer that much more likely. Clamped away from 0 and 1 so no
    single answer can ever drive a hypothesis to exactly zero — questions are
    evidence, not verdicts.
    """
    base = 0.5 + float(disc.favours.get(problem_type, 0.0)) \
                - float(disc.disfavours.get(problem_type, 0.0))
    return min(0.95, max(0.05, base))


def expected_gain(disc, dist: dict[str, float]) -> tuple[float, bool]:
    """Expected reduction in bits of uncertainty from asking this question.

    Returns (bits, estimated). Free-text discriminators cannot be modelled this
    way — there is no option set to integrate over — so they get a conservative
    fraction of current entropy and are flagged `estimated`, which keeps them
    below any real question that would actually split the field.
    """
    if not dist:
        return 0.0, True
    if disc.kind != "choice" or len(disc.options) < 2:
        return 0.25 * _entropy(dist), True

    p_aff = sum(p * _likelihood_affirmative(disc, pt) for pt, p in dist.items())
    p_neg = 1.0 - p_aff
    if p_aff <= 0 or p_neg <= 0:
        return 0.0, False

    post_aff = _normalise({pt: p * _likelihood_affirmative(disc, pt)
                           for pt, p in dist.items()})
    post_neg = _normalise({pt: p * (1.0 - _likelihood_affirmative(disc, pt))
                           for pt, p in dist.items()})
    expected_h = p_aff * _entropy(post_aff) + p_neg * _entropy(post_neg)
    return max(0.0, _entropy(dist) - expected_h), False


def rank_discriminators(hyps: list[Hypothesis], answered: set[str] | None = None,
                        limit: int = 3) -> list[dict]:
    """The questions worth asking, best first.

    Collected from every live hypothesis — a question declared on `duplicate_charge`
    is asked to rule OUT duplicate_charge just as often as to confirm it, and
    scoping questions to the leader would blind Agent X to its own best rival.
    """
    answered = answered or set()
    dist = _normalise({h.problem_type: h.posterior for h in hyps if h.posterior > 0.02})
    if len(dist) < 2:
        return []

    cat = catalogue()
    pool: dict[str, tuple] = {}
    for pt in dist:
        d = cat.get(pt)
        if not d:
            continue
        for disc in d.discriminators:
            if disc.id in answered or disc.id in pool:
                continue
            pool[disc.id] = (disc, pt)

    ranked = []
    for did, (disc, owner) in pool.items():
        bits, estimated = expected_gain(disc, dist)
        ranked.append({
            "id": did, "question": disc.question, "kind": disc.kind,
            "options": list(disc.options), "why": disc.why,
            "declared_on": owner,
            "expected_bits": round(bits, 3), "estimated": estimated,
            "separates": sorted(set(disc.favours) | set(disc.disfavours)),
        })
    ranked.sort(key=lambda q: (-q["expected_bits"], q["estimated"]))
    return ranked[:limit]


def apply_answer(hyps: list[Hypothesis], discriminator_id: str,
                 answer: str) -> list[Hypothesis]:
    """Bayesian update from one answer, using the same likelihood model that
    priced the question. Same model both times is what makes the update honest:
    a system that ranks with one model and updates with another can present a
    question as decisive and then ignore the answer.
    """
    cat = catalogue()
    disc = None
    for d in cat.values():
        for x in d.discriminators:
            if x.id == discriminator_id:
                disc = x
                break
        if disc:
            break
    if disc is None or not hyps:
        return hyps

    opts = [o.lower() for o in disc.options]
    a = (answer or "").strip().lower()
    idx = next((i for i, o in enumerate(opts) if o == a), None)
    if idx is None:
        idx = next((i for i, o in enumerate(opts) if a and (a in o or o in a)), None)
    if idx is None or idx >= 2:
        return hyps                       # "not sure", or free text: no update

    affirmative = idx == 0
    updated = []
    for h in hyps:
        like = _likelihood_affirmative(disc, h.problem_type)
        p = h.posterior * (like if affirmative else (1.0 - like))
        updated.append(Hypothesis(
            problem_type=h.problem_type, domain=h.domain, label=h.label,
            prior=h.prior, score=h.score, posterior=p,
            signals=h.signals + [f'answer to "{disc.id}": {answer}'],
            rationale=h.rationale))
    total = sum(h.posterior for h in updated)
    for h in updated:
        h.posterior = h.posterior / total if total else 0.0
    updated.sort(key=lambda h: -h.posterior)
    return updated


# ─────────────────────────────────────────────────────────────────────────────
# LLM fusion — bounded, optional, and recorded
# ─────────────────────────────────────────────────────────────────────────────
_LLM_SYSTEM = (
    "You classify a consumer complaint against a FIXED catalogue of problem types. "
    "Return ONLY JSON: {\"scores\": {\"<problem_type>\": <0..1>, ...}, "
    "\"entities\": [{\"kind\": \"merchant|order|payment|booking|amount|date|product|account\", "
    "\"value\": \"...\"}], \"note\": \"one sentence\"}. "
    "Rules: use ONLY problem_type values from the provided list — never invent one. "
    "Score how well each candidate explains the complaint. If the complaint is "
    "genuinely ambiguous, give several candidates similar scores rather than "
    "picking one. Extract entities only if the text states them; never guess a "
    "merchant or an amount."
)


def llm_refine(text: str, hyps: list[Hypothesis], timeout_ok: bool = True) -> tuple[list[Hypothesis], str]:
    """Let a model redistribute mass among candidates the catalogue already found.

    Bounded three ways, each of which closes a specific failure:
      * candidates are fixed, so the model cannot invent a problem type nobody
        has evidence rules or a provider for;
      * scores are FUSED with the deterministic posterior at equal weight rather
        than replacing it, so a confidently wrong model cannot erase lexical
        evidence;
      * failure is silent and reported, never fatal — the deterministic path is
        the product, and the model is an improvement to it.
    """
    if not hyps or not timeout_ok:
        return hyps, "model not consulted"
    candidates = [h.problem_type for h in hyps[:8]]
    cat = catalogue()
    menu = "\n".join(f"- {pt}: {cat[pt].summary.strip()[:140]}" for pt in candidates if pt in cat)
    try:
        from llm import client
        got = client.chat_json(_LLM_SYSTEM,
                               f"Candidates:\n{menu}\n\nComplaint:\n{text.strip()[:1500]}",
                               task="classify")
    except Exception as e:
        return hyps, f"model unavailable ({type(e).__name__}); deterministic scoring stands"

    scores = got.get("scores") if isinstance(got, dict) else None
    if not isinstance(scores, dict) or not scores:
        return hyps, "model returned no usable scores; deterministic scoring stands"

    clean = {}
    for pt, v in scores.items():
        if pt in cat:
            try:
                clean[pt] = min(1.0, max(0.0, float(v)))
            except (TypeError, ValueError):
                continue
    if not clean:
        return hyps, "model named no known problem type; deterministic scoring stands"

    total_llm = sum(clean.values()) or 1.0
    fused = []
    for h in hyps:
        llm_p = clean.get(h.problem_type, 0.0) / total_llm
        # Geometric fusion: an opinion neither side holds cannot survive, and
        # neither side alone can drive a hypothesis to certainty.
        p = math.sqrt(max(h.posterior, 1e-6) * max(llm_p, 1e-6))
        fused.append(Hypothesis(
            problem_type=h.problem_type, domain=h.domain, label=h.label,
            prior=h.prior, score=h.score, posterior=p,
            signals=h.signals + ([f"model score {llm_p:.2f}"] if h.problem_type in clean else []),
            rationale=h.rationale))
    t = sum(h.posterior for h in fused)
    for h in fused:
        h.posterior = h.posterior / t if t else 0.0
    fused.sort(key=lambda h: -h.posterior)
    return fused, (got.get("note") or "model scores fused with catalogue evidence")[:200]


def understand(text: str, *, evidence_kinds: tuple[str, ...] = (),
               domain_hint: str | None = None, use_llm: bool = True) -> Understanding:
    """The entry point: narrative in, distribution plus entities out."""
    hyps = hypotheses(text, evidence_kinds, domain_hint)
    note, used = "", False
    if use_llm and len(hyps) > 1:
        hyps, note = llm_refine(text, hyps)
        used = "fused" in note or "model score" in " ".join(
            s for h in hyps for s in h.signals)
    ents = extract_entities(text)
    u = Understanding(text=text, hypotheses=hyps, entities=ents,
                      ambiguous=is_ambiguous(hyps), margin=margin_of(hyps),
                      llm_used=used, llm_note=note)
    u.residual = round(residual_mass(hyps), 4)
    return u
