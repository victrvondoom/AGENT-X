"""
Agent X — resolution engine evaluation harness.

`evals/rrs.py` measures the erasure claim. This measures the resolution claim,
and it exists because "the tests pass" is not an answer to *how well does it
work*. A test asserts a property holds; an eval reports a number that can get
worse.

Seven measurements, each attached to a specific claim the product makes:

  1. CLASSIFICATION       does it identify the right problem from a sentence?
  2. AMBIGUITY CALIBRATION does it stay plural when a sentence genuinely is,
                          and commit when it genuinely is not? (Both directions
                          matter — an engine that is always uncertain is as
                          useless as one that is never uncertain.)
  3. QUESTION EFFICIENCY  how many questions to collapse an ambiguous case?
  4. POLICY DETERMINISM   same facts in, same verdict out, every time — and
                          `unknown` where a fact is genuinely absent.
  5. PLAN VALIDITY        what share of composed plans pass the validator?
  6. LETTER GROUNDING     what share of outbound drafts trace every figure?
  7. GOVERNOR SAFETY      what share of irreversible/high-risk actions demand
                          explicit authorisation? (Anything below 100% is a bug.)

Everything runs with `use_llm=False` against the local SQLite engine, so the
score is reproducible on any machine with no database, no API key and no
network. That is deliberate: a benchmark you cannot re-run is a claim, not a
measurement.

Usage:  python -m evals.resolution        (add --verbose to see every miss)
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

os.environ.setdefault("AGENT_X_ENGINE", "sqlite")

from agentx import (capabilities, governor, letters, planner,  # noqa: E402
                    policy, store, understanding)
from agentx import case as case_mod                             # noqa: E402
from agentx.evidence import graph as egraph                      # noqa: E402
from agentx.execution import actions as A                        # noqa: E402
from agentx.execution import providers                           # noqa: E402
from agentx.ontology import catalogue, get as get_definition     # noqa: E402

VERBOSE = "--verbose" in sys.argv

# ─────────────────────────────────────────────────────────────────────────────
# 1-2. the labelled set
# ─────────────────────────────────────────────────────────────────────────────
# (narrative, expected problem_type, should_be_ambiguous)
#
# `None` as the expected type means "the catalogue does not model this and the
# engine should say so" — an engine that confidently classifies nonsense is
# worse than one that declines, so those cases count.
LABELLED: list[tuple[str, str | None, bool]] = [
    # — unambiguous, one clear reading —
    ("Amazon charged me twice for something I only received once.", "duplicate_charge", False),
    ("Kartly billed me twice for the same order on the same day.", "duplicate_charge", False),
    ("My hotel cancelled my booking two days before check-in.", "hotel_booking_cancelled", False),
    ("The property cancelled my reservation without telling me.", "hotel_booking_cancelled", False),
    ("My flight was delayed and I arrived over four hours late.", "flight_delay_compensation", False),
    ("The airline lost my baggage and it never turned up.", "baggage_lost", False),
    ("My luggage never arrived at the carousel.", "baggage_lost", False),
    ("They cancelled my flight the day before departure.", "flight_cancelled", False),
    ("I was denied boarding because the flight was overbooked.", "denied_boarding", False),
    ("The product I received is not what I ordered.", "wrong_item_received", False),
    ("They sent the wrong size, I ordered a large.", "wrong_item_received", False),
    ("My parcel never arrived even though tracking says delivered.", "order_not_received", False),
    ("The item arrived smashed and unusable.", "damaged_item", False),
    ("This company refuses to refund me.", "return_refused", False),
    ("They promised a refund weeks ago and it never came.", "refund_not_received", False),
    ("This subscription renewed without me realising.", "subscription_renewal_unexpected", False),
    ("My annual plan auto-renewed and I never used it.", "subscription_renewal_unexpected", False),
    ("I can't cancel my subscription, there is no cancel button.", "subscription_cancellation_blocked", False),
    ("I want to cancel this service.", "cancel_service", False),
    ("I don't understand this bill.", "bill_unclear", False),
    ("What is this charge on my bill?", "bill_unclear", False),
    ("My mobile bill is much higher than my plan.", "telecom_bill_dispute", False),
    ("My energy bill is based on an estimated reading.", "utility_bill_dispute", False),
    ("They charged more than the advertised price.", "price_discrepancy", False),
    ("The builder took my deposit and never did the work.", "service_not_delivered", False),
    ("My insurance claim was denied.", "insurance_claim_denied", False),
    ("The laptop broke and it is still under warranty.", "warranty_claim", False),
    ("My gym membership renewed for another year.", "membership_auto_renewal", False),
    ("I don't recognise this charge, I never bought this.", "fraud_suspected", False),
    ("There's a pending charge on my card I didn't expect.", "authorization_hold", False),

    # — genuinely ambiguous: several readings must stay live —
    ("They charged me again", "duplicate_charge", True),
    ("Why have they charged me again this month", "duplicate_charge", True),
    ("There's another charge from them", "duplicate_charge", True),

    # — outside the catalogue: the engine should decline, not guess —
    ("qwerty zxcvb asdfg nonsense tokens", None, True),
    ("What is the capital of France?", None, True),
]


def _score_classification() -> dict:
    hits = misses = declined_right = declined_wrong = 0
    detail = []
    for text, expected, _amb in LABELLED:
        u = understanding.understand(text, use_llm=False)
        top = u.top.problem_type if u.top else None
        if expected is None:
            # Correct behaviour is a high residual / no confident match.
            ok = (top is None) or u.residual >= 0.5 or u.ambiguous
            declined_right += int(ok)
            declined_wrong += int(not ok)
            if not ok:
                detail.append(f"  should have declined: {text!r} -> {top}")
            continue
        if top == expected:
            hits += 1
        else:
            misses += 1
            detail.append(f"  {text!r}\n      expected {expected}, got {top}")
    graded = hits + misses
    return {"top1_accuracy": round(hits / graded, 3) if graded else 0.0,
            "graded": graded, "hits": hits, "misses": misses,
            "declined_correctly": declined_right, "declined_wrongly": declined_wrong,
            "detail": detail}


def _score_ambiguity() -> dict:
    """Both directions. Staying plural on a clear sentence is as wrong as
    collapsing a genuinely ambiguous one."""
    tp = tn = fp = fn = 0
    detail = []
    for text, expected, should_be_ambiguous in LABELLED:
        u = understanding.understand(text, use_llm=False)
        amb = u.ambiguous
        if should_be_ambiguous and amb:
            tp += 1
        elif should_be_ambiguous and not amb:
            fn += 1
            detail.append(f"  collapsed a genuinely ambiguous case: {text!r}")
        elif (not should_be_ambiguous) and (not amb):
            tn += 1
        else:
            fp += 1
            detail.append(f"  hedged on a clear case: {text!r}")
    total = tp + tn + fp + fn
    return {"calibration": round((tp + tn) / total, 3) if total else 0.0,
            "held_ambiguity": tp, "missed_ambiguity": fn,
            "committed_correctly": tn, "over_hedged": fp, "detail": detail}


def _score_question_efficiency() -> dict:
    """How many questions to collapse an ambiguous case.

    Simulated by answering each ranked discriminator with the option that most
    favours the true reading — the best case for the engine, which is the right
    frame: it measures whether the QUESTIONS are well chosen, not whether the
    user answers well.
    """
    rounds = []
    for text, expected, should_be_ambiguous in LABELLED:
        if not should_be_ambiguous or expected is None:
            continue
        u = understanding.understand(text, use_llm=False)
        hyps, asked = u.hypotheses, 0
        for _ in range(4):
            if not understanding.is_ambiguous(hyps):
                break
            qs = understanding.rank_discriminators(hyps, limit=1)
            if not qs or not qs[0]["options"]:
                break
            hyps = understanding.apply_answer(hyps, qs[0]["id"], qs[0]["options"][0])
            asked += 1
        rounds.append(asked)
    return {"cases": len(rounds),
            "avg_questions_to_resolve": round(sum(rounds) / len(rounds), 2) if rounds else None,
            "max_questions": max(rounds) if rounds else None}


# ─────────────────────────────────────────────────────────────────────────────
# 4. policy determinism
# ─────────────────────────────────────────────────────────────────────────────
def _score_policy() -> dict:
    """Same input, same verdict — and `unknown` when a fact is genuinely absent.

    Run the same evaluation repeatedly and assert the verdicts never drift. A
    policy engine that answers differently on Tuesday is not a policy engine.
    """
    definition = get_definition("flight_delay_compensation")
    assert definition is not None, "flight_delay_compensation must stay in the ontology"
    facts = {"flight.delay_minutes": 240, "flight.distance_km": 1850,
             "flight.disruption_reason": "technical"}
    runs = []
    for _ in range(20):
        f = policy.analyse(definition, facts, jurisdiction="EU")
        runs.append(tuple(sorted((x.policy.id, x.applies) for x in f)))
    stable = len(set(runs)) == 1

    # `unknown` must appear where a required fact is missing, not a guess.
    #
    # The invariant is specifically about CONDITIONAL policies: one that declares
    # conditions on facts the case does not have must resolve to `unknown`. A
    # policy with no conditions at all (Montreal Convention, a merchant's own
    # terms) applying unconditionally is correct, not a guess — an earlier
    # version of this harness counted those and reported a false failure.
    sparse = policy.analyse(definition, {}, jurisdiction="EU")
    unknowns = sum(1 for x in sparse if x.applies == "unknown")
    guesses = sum(1 for x in sparse
                  if x.applies == "yes" and x.policy.conditions)
    unconditional = sum(1 for x in sparse
                        if x.applies == "yes" and not x.policy.conditions)

    # every problem type's cited policies must exist
    dangling = policy.missing_references()

    # entitlement must be computed, not invented
    eu = next((x for x in policy.analyse(definition, facts, jurisdiction="EU")
               if x.policy.id == "eu261"), None)
    return {"deterministic_over_20_runs": stable,
            "unknown_when_facts_absent": unknowns,
            "guessed_when_facts_absent": guesses,
            "unconditional_policies_applied": unconditional,
            "dangling_policy_references": len(dangling),
            "eu261_entitlement_minor": eu.entitlement_minor if eu else None,
            "policies_in_corpus": len(policy.corpus())}


# ─────────────────────────────────────────────────────────────────────────────
# 5. plan validity across the whole catalogue
# ─────────────────────────────────────────────────────────────────────────────
def _score_plans() -> dict:
    """Compose a plan for every (problem type x declared remedy) in the
    catalogue and validate it. This is the broadest test of the ontology: a
    definition whose declared remedy cannot produce a valid plan is a definition
    that will strand a real case.
    """
    providers.bootstrap()
    ok = bad = 0
    detail = []
    fake_case = {"id": "PX-EVAL0", "workspace": "default"}
    for pt, definition in sorted(catalogue().items()):
        for remedy in definition.resolution_strategies:
            plan = planner.compose(
                case=fake_case, definition=definition, remedy=remedy,
                findings=[], missing_evidence=[], counterparty="Kartly",
                amount_minor=5000, currency="GBP")
            v = plan.validation
            if v.get("ok"):
                ok += 1
            else:
                bad += 1
                detail.append(f"  {pt} / {remedy}: {v.get('errors', [])[:1]}")
    total = ok + bad
    return {"plans_composed": total, "valid": ok, "invalid": bad,
            "validity_rate": round(ok / total, 3) if total else 0.0,
            "detail": detail}


# ─────────────────────────────────────────────────────────────────────────────
# 6. letter grounding
# ─────────────────────────────────────────────────────────────────────────────
def _score_grounding() -> dict:
    """Every figure in an outbound draft must trace to the fact graph, and an
    ungrounded rewrite must be rejected."""
    store.ensure_schema()
    grounded = total = 0
    rejected_ungrounded = 0
    with store.connect() as conn:
        for i, (text, expected, _a) in enumerate(LABELLED[:12]):
            if expected is None:
                continue
            c = case_mod.create(conn, description=text)
            c = case_mod.update(conn, c["id"], confidence=0.9,
                                problem_type=expected, amount_minor=1200,
                                currency="GBP")
            assert c is not None, "just-created case must still exist"
            ev = egraph.add_evidence(conn, case_id=c["id"], workspace="default",
                                     subject=c["subject"], kind="transaction",
                                     text="Total charged: 12.00 GBP")
            from agentx.evidence import extract as _ex
            egraph.add_facts(conn, c["id"], ev["id"],
                             _ex.extract("Total charged: 12.00 GBP", "transaction",
                                         use_llm=False))
            body, _subj = letters.compose(conn, c, {
                "remedy": "merchant_refund", "counterparty": "Kartly",
                "amount_minor": 1200, "currency": "GBP"})
            total += 1
            if letters.grounding_report(body, conn, c["id"])["grounded"]:
                grounded += 1
            # an invented figure must fail
            if not letters.grounding_report(
                    "Refund 950000.00 GBP under reference ZZ99887766.",
                    conn, c["id"])["grounded"]:
                rejected_ungrounded += 1
    return {"drafts": total,
            "grounded_rate": round(grounded / total, 3) if total else 0.0,
            "ungrounded_rejected_rate": round(rejected_ungrounded / total, 3) if total else 0.0}


# ─────────────────────────────────────────────────────────────────────────────
# 7. governor safety
# ─────────────────────────────────────────────────────────────────────────────
def _score_governor() -> dict:
    """Sweep every action verb x autonomy level x confidence and count the ways a
    consequential action could escape approval.

    The invariants below are the ones the governor actually DOCUMENTS, not a
    stricter reading of them — an eval that fails on correct behaviour teaches
    people to ignore it:

      * irreversible AND high-risk  -> explicit approval at EVERY level, including 4
      * writes externally, level <= 2 -> confirmed before sending ("prepare and confirm")
      * irreversible, level <= 3    -> approval required; level 4 is precisely the
                                       level that may run irreversible work under a
                                       standing policy, so it is exempt by design

    Note the third: an earlier version of this harness demanded approval for
    irreversible actions at level 4 too, which contradicted the documented
    contract and reported a false failure.
    """
    escapes = []
    checked = 0
    for verb, spec in A.ACTIONS.items():
        for level in range(0, 5):
            for conf in (0.0, 0.6, 0.9, 1.0):
                cap = next((c for c in capabilities.REGISTRY.values()
                            if verb in c.actions), None)
                risk = cap.risk if cap else spec.risk
                v = governor.assess(action=verb, capability=cap, case_level=level,
                                    risk=risk, confidence=conf,
                                    amount_minor=1000, currency="GBP")
                checked += 1
                if not v.allow:
                    continue
                irreversible = not spec.reversible
                if irreversible and risk == "high" and not v.requires_authorization:
                    escapes.append(
                        f"{verb} @ L{level} conf {conf}: irreversible+high ran "
                        f"unattended ({v.rule})")
                if spec.writes_externally and level <= 2 and not v.requires_authorization:
                    escapes.append(
                        f"{verb} @ L{level}: left the system without confirmation")
                if irreversible and level <= 3 and not v.requires_authorization:
                    escapes.append(
                        f"{verb} @ L{level}: irreversible below level 4 ({v.rule})")
    return {"combinations_checked": checked,
            "unauthorised_escapes": len(escapes),
            "safe": not escapes, "detail": sorted(set(escapes))[:10]}


# ─────────────────────────────────────────────────────────────────────────────
# 8. end to end
# ─────────────────────────────────────────────────────────────────────────────
def _score_end_to_end() -> dict:
    from agentx import chain, demo
    store.ensure_schema()
    providers.bootstrap()
    resolved = signed = intact = 0
    keys = sorted(demo.SCENARIOS)
    with store.connect() as conn:
        demo.reset(conn)
        for k in keys:
            r = demo.run(conn, k, use_llm=False)
            resolved += int(r["final_state"] == "RESOLVED")
            signed += int(bool(r["receipt_signed"]))
            intact += int(bool(r["chain"]["ok"]))
    n = len(keys)
    return {"scenarios": n, "resolved": resolved, "receipts_signed": signed,
            "chains_intact": intact,
            "resolution_rate": round(resolved / n, 3) if n else 0.0}


# ─────────────────────────────────────────────────────────────────────────────
# 9. research and citation checking
# ─────────────────────────────────────────────────────────────────────────────
# Two numbers that can get worse and one invariant that must not move.
#
# Retrieval is scored in BOTH directions on purpose. Recall alone rewards a
# system that returns its best guess for everything, which is the failure mode
# that matters here: a consumer reading airline regulations under a hotel dispute
# is worse off than one who was told nothing was found. So `silence` — queries
# outside the corpus that correctly retrieve nothing — is scored alongside reach.
#
# The invariant is citation safety: a claim contradicted by its own source must
# never come back `verified`. That is the check standing between a hallucinated
# regulation and an outbound letter.
_RESEARCH_COVERED = [
    "unauthorized transaction on my credit card, bank refuses to reverse it",
    "my flight was cancelled without notice, what compensation am I owed",
    "the builder has not handed over possession of my flat after three years",
    "my landlord is refusing to return my security deposit",
    "I filed an RTI application and the department never replied",
    "the airline lost my checked baggage on an international flight",
]

_RESEARCH_UNCOVERED = [
    "my hotel in Paris overcharged me for the minibar",
    "my gym membership auto-renewed and I want it cancelled",
    "netflix charged me twice this month",
    "the restaurant gave me food poisoning",
    "my hotel cancelled my booking on arrival and I paid more elsewhere",
]

# (claim, source text, the only acceptable verdict)
_CITATION_CASES = [
    ("shadow reversal of the disputed amount within 10 working days",
     "The bank must complete a shadow reversal of the disputed amount within 10 "
     "working days of the customer reporting an unauthorized transaction.",
     "verified"),
    # The one a word-overlap check gets wrong: every word but the number matches.
    ("shadow reversal of the disputed amount within 30 working days",
     "The bank must complete a shadow reversal of the disputed amount within 10 "
     "working days of the customer reporting an unauthorized transaction.",
     "conflicting"),
    ("the airline must provide hotel accommodation and meal vouchers",
     "The bank must complete a shadow reversal within 10 working days.",
     "unsupported"),
]


def _score_research() -> dict:
    from agentx import knowledge

    stats = knowledge.stats()
    reached = sum(1 for q in _RESEARCH_COVERED if knowledge.search(q))
    silent = sum(1 for q in _RESEARCH_UNCOVERED if not knowledge.search(q))

    correct = 0
    unsafe = 0
    detail = []
    for claim, source, expected in _CITATION_CASES:
        check = knowledge.verify_citation(claim, [{"id": "s", "text": source}])
        correct += int(check.verdict == expected)
        # The invariant: a claim its source contradicts must never be stateable.
        if expected != "verified" and check.safe_to_state:
            unsafe += 1
        detail.append(f"     {check.verdict:12s} (expected {expected:12s}) {claim[:44]}")

    return {
        "corpus_documents": stats["documents"],
        "corpus_passages": stats["passages"],
        "sectors": len(stats["sectors"]),
        "covered_queries": len(_RESEARCH_COVERED),
        "reached": reached,
        "reach_rate": round(reached / len(_RESEARCH_COVERED), 3),
        "uncovered_queries": len(_RESEARCH_UNCOVERED),
        "silent": silent,
        "silence_rate": round(silent / len(_RESEARCH_UNCOVERED), 3),
        "citation_cases": len(_CITATION_CASES),
        "citation_correct": correct,
        "citation_accuracy": round(correct / len(_CITATION_CASES), 3),
        "unverified_claims_marked_safe": unsafe,
        "detail": detail,
    }


# ─────────────────────────────────────────────────────────────────────────────
def main() -> dict:
    import tempfile
    store.use_sqlite(os.path.join(tempfile.mkdtemp(prefix="agentx-eval-"), "eval.db"))

    print("=" * 70)
    print("Agent X — resolution engine evaluation")
    print("deterministic (use_llm=False) · local engine · no network")
    print("=" * 70)

    t0 = time.time()
    results = {}

    print("\n1. CLASSIFICATION")
    r = _score_classification(); results["classification"] = r
    print(f"   top-1 accuracy on {r['graded']} labelled narratives : "
          f"{r['top1_accuracy']:.1%}  ({r['hits']}/{r['graded']})")
    print(f"   out-of-catalogue inputs correctly declined       : "
          f"{r['declined_correctly']}/{r['declined_correctly'] + r['declined_wrongly']}")
    if VERBOSE:
        for d in r["detail"]:
            print(d)

    print("\n2. AMBIGUITY CALIBRATION")
    r = _score_ambiguity(); results["ambiguity"] = r
    print(f"   calibration (both directions)                    : {r['calibration']:.1%}")
    print(f"   held ambiguity when it existed                   : {r['held_ambiguity']}")
    print(f"   committed when the sentence was clear            : {r['committed_correctly']}")
    print(f"   collapsed a genuinely ambiguous case             : {r['missed_ambiguity']}")
    print(f"   hedged on a clear case                           : {r['over_hedged']}")
    if VERBOSE:
        for d in r["detail"]:
            print(d)

    print("\n3. QUESTION EFFICIENCY")
    r = _score_question_efficiency(); results["questions"] = r
    print(f"   ambiguous cases                                  : {r['cases']}")
    print(f"   avg questions to collapse                        : {r['avg_questions_to_resolve']}")
    print(f"   worst case                                       : {r['max_questions']}")

    print("\n4. POLICY DETERMINISM")
    r = _score_policy(); results["policy"] = r
    print(f"   identical verdicts over 20 runs                  : {r['deterministic_over_20_runs']}")
    print(f"   `unknown` where a fact was absent                : {r['unknown_when_facts_absent']}")
    print(f"   GUESSED on a conditional rule (must be 0)        : {r['guessed_when_facts_absent']}")
    print(f"   unconditional rules applied (legitimately)       : {r['unconditional_policies_applied']}")
    print(f"   dangling policy references (must be 0)           : {r['dangling_policy_references']}")
    print(f"   EU261 entitlement computed, minor units          : {r['eu261_entitlement_minor']}")

    print("\n5. PLAN VALIDITY (every problem type x every declared remedy)")
    r = _score_plans(); results["plans"] = r
    print(f"   plans composed                                   : {r['plans_composed']}")
    print(f"   pass the deterministic validator                 : {r['validity_rate']:.1%}")
    if VERBOSE:
        for d in r["detail"][:20]:
            print(d)

    print("\n6. LETTER GROUNDING")
    r = _score_grounding(); results["grounding"] = r
    print(f"   drafts checked                                   : {r['drafts']}")
    print(f"   every figure traceable to evidence               : {r['grounded_rate']:.1%}")
    print(f"   invented figures rejected                        : {r['ungrounded_rejected_rate']:.1%}")

    print("\n7. GOVERNOR SAFETY")
    r = _score_governor(); results["governor"] = r
    print(f"   action x level x confidence combinations         : {r['combinations_checked']}")
    print(f"   consequential actions escaping approval (must be 0): {r['unauthorised_escapes']}")
    if VERBOSE:
        for d in r["detail"]:
            print("  ", d)

    print("\n8. END TO END (sandbox scenarios)")
    r = _score_end_to_end(); results["end_to_end"] = r
    print(f"   scenarios run                                    : {r['scenarios']}")
    print(f"   resolved                                         : {r['resolved']}/{r['scenarios']}")
    print(f"   receipts signed                                  : {r['receipts_signed']}/{r['scenarios']}")
    print(f"   chains intact                                    : {r['chains_intact']}/{r['scenarios']}")

    print("\n9. RESEARCH & CITATION CHECKING")
    r = _score_research(); results["research"] = r
    print(f"   corpus                                           : "
          f"{r['corpus_documents']} docs · {r['corpus_passages']} passages · "
          f"{r['sectors']} sectors")
    print(f"   covered queries that reached the corpus          : "
          f"{r['reach_rate']:.1%}  ({r['reached']}/{r['covered_queries']})")
    print(f"   uncovered queries correctly answered with silence: "
          f"{r['silence_rate']:.1%}  ({r['silent']}/{r['uncovered_queries']})")
    print(f"   citation verdicts correct                        : "
          f"{r['citation_accuracy']:.1%}  ({r['citation_correct']}/{r['citation_cases']})")
    print(f"   unverified claims marked safe to state (must be 0): "
          f"{r['unverified_claims_marked_safe']}")
    if VERBOSE:
        for d in r["detail"]:
            print(d)

    print("\n" + "=" * 70)
    hard_failures = [
        ("policy guessed on a conditional rule", results["policy"]["guessed_when_facts_absent"] > 0),
        ("dangling policy references", results["policy"]["dangling_policy_references"] > 0),
        ("governor allowed an unauthorised action", not results["governor"]["safe"]),
        ("a scenario failed to resolve",
         results["end_to_end"]["resolved"] < results["end_to_end"]["scenarios"]),
        ("an invented figure passed grounding",
         results["grounding"]["ungrounded_rejected_rate"] < 1.0),
        ("an unverified citation was marked safe to state",
         results["research"]["unverified_claims_marked_safe"] > 0),
    ]
    broken = [name for name, failed in hard_failures if failed]
    print(f"HEADLINE  classification {results['classification']['top1_accuracy']:.0%}"
          f" · ambiguity {results['ambiguity']['calibration']:.0%}"
          f" · plans {results['plans']['validity_rate']:.0%}"
          f" · grounding {results['grounding']['grounded_rate']:.0%}"
          f" · research {results['research']['reach_rate']:.0%}/"
          f"{results['research']['silence_rate']:.0%}"
          f" · e2e {results['end_to_end']['resolution_rate']:.0%}")
    if broken:
        print("SAFETY    FAILED: " + "; ".join(broken))
    else:
        print("SAFETY    all invariants hold (no guessed policy, no unauthorised "
              "action,\n          no ungrounded figure, no unverified citation)")
    print(f"ran in {time.time() - t0:.1f}s")
    print("=" * 70)
    results["safety_invariants_hold"] = not broken
    return results


if __name__ == "__main__":
    out = main()
    sys.exit(0 if out["safety_invariants_hold"] else 1)
