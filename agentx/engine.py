"""
The resolution engine — the pipeline the whole product is shaped around.

    Consumer problem
        → Problem understanding        (a distribution, not a label)
        → Evidence collection          (typed, located, hashed)
        → Policy / rights analysis     (deterministic, cited)
        → Eligibility & confidence     (ranked remedies, named blockers)
        → Resolution strategy          (a validated execution graph)
        → Required authorization       (governed by risk and reversibility)
        → Action execution             (provider-independent, recorded)
        → External response            (what they actually said)
        → Follow-up                    (case-aware, on a clock)
        → Outcome verification         (re-read their records, not their reply)
        → Evidence-backed resolution record

Every function here is a stage of that pipeline. There is no second pipeline for a
second vertical: an airline delay and a duplicate charge walk the same eleven steps
and differ only in the YAML that describes them and the provider that serves them.

THE ENGINE DOES NOT DECIDE ANYTHING ITSELF

It composes modules that each own one decision and can each be tested alone:
understanding owns the distribution, policy owns applicability, eligibility owns
ranking, the planner owns structure, the governor owns permission, the runner owns
execution, the follow-up agent owns time. This module owns only the ORDER, and the
transitions between stages. That is why it is short relative to what it does.
"""
from __future__ import annotations

from agentx import (capabilities, chain, eligibility, ids, normalize, planner,
                    policy, research, stages, tradeoffs)
from agentx import case as case_mod
from agentx import store, understanding
from agentx.evidence import contradiction, extract, graph as egraph, package as pkg
from agentx.execution import providers, runner
from agentx.ontology import EVIDENCE_KINDS, get as get_definition

# Below this, Agent X asks instead of acting. Sits here rather than in the governor
# because it is about whether the case is UNDERSTOOD, not about whether an action
# is permitted — two different questions that a single threshold would conflate.
MIN_CONFIDENCE_TO_PLAN = 0.35


def _ready() -> None:
    store.ensure_schema()
    providers.bootstrap()


# ─────────────────────────────────────────────────────────────────────────────
# 1. intake
# ─────────────────────────────────────────────────────────────────────────────
def intake(conn, *, description: str, user_ref: str = "demo-user",
           workspace: str = "default", autonomy_level: int = 2,
           evidence: list[dict] | None = None, use_llm: bool = True) -> dict:
    """Open a case from a sentence, plus whatever the user attached.

    The narrative is stored as evidence in its own right (`statement_note`), which
    is not a formality: it is what lets a fact the user simply asserted still obey
    the no-fact-without-a-link rule, and what lets their own words be crypto-
    shredded along with everything else if they ask.
    """
    _ready()
    c = case_mod.create(conn, description=description, user_ref=user_ref,
                        workspace=workspace, autonomy_level=autonomy_level)

    egraph.add_evidence(conn, case_id=c["id"], workspace=workspace,
                        subject=c["subject"], kind="statement_note",
                        text=description, filename="what-you-told-agentx.txt",
                        media_type="text/plain", trust="user_capture")

    for item in evidence or []:
        attach(conn, c["id"], kind=item.get("kind", "screenshot"),
               text=item.get("text", ""), filename=item.get("filename"),
               raw=item.get("raw"), use_llm=use_llm, reanalyse=False)

    return understand(conn, c["id"], use_llm=use_llm)


# ─────────────────────────────────────────────────────────────────────────────
# 2. understanding
# ─────────────────────────────────────────────────────────────────────────────
def understand(conn, case_id: str, *, use_llm: bool = True) -> dict:
    """Re-derive the hypothesis set from everything currently known."""
    c = case_mod.get(conn, case_id)
    if not c:
        raise ValueError(f"no such case {case_id}")

    kinds = egraph.evidence_kinds(conn, case_id)
    answered = _answered_discriminators(conn, case_id)

    u = understanding.understand(c["description"], evidence_kinds=kinds,
                                 use_llm=use_llm)
    for did, value in answered.items():
        u.hypotheses = understanding.apply_answer(u.hypotheses, did, value)
    u.ambiguous = understanding.is_ambiguous(u.hypotheses)
    u.margin = understanding.margin_of(u.hypotheses)
    u.residual = understanding.residual_mass(u.hypotheses)

    case_mod.save_interpretations(conn, case_id, u.hypotheses[:8])
    for e in u.entities:
        case_mod.add_entity(conn, case_id, kind=e["kind"], value=e["value"],
                            confidence=e["confidence"], source=e["source"],
                            normalized=e.get("normalized"))

    top = u.top
    chain.append(conn, case_id, "understanding", "AGENT",
                 {"problem_type": top.problem_type if top else None,
                  "domain": top.domain if top else None,
                  "confidence": round(top.posterior, 3) if top else 0.0,
                  "residual": round(u.residual, 3),
                  "alternatives": [{"problem_type": h.problem_type,
                                    "posterior": round(h.posterior, 3)}
                                   for h in u.hypotheses[1:5]],
                  "llm_used": u.llm_used, "reason": u.llm_note})

    if not top:
        case_mod.ask(conn, case_id,
                     question="Can you say a bit more about what went wrong?",
                     why="Agent X could not match this to any problem it knows how to "
                         "resolve, so it would rather ask than guess.",
                     kind="fact")
        general = get_definition("general_consumer_problem")
        if general is None:
            # Definition missing from this deployment's catalogue — fall back to the
            # old behaviour rather than crash on a KeyError-shaped None below.
            _move(conn, case_id, "NEEDS_INPUT",
                  "the description does not match any known problem type")
            return snapshot(conn, case_id)
        chain.append(conn, case_id, "understanding.fallback", "AGENT",
                     {"reason": "no catalogue entry scored any signal against this "
                               "narrative; proceeding on the general consumer-problem "
                               "path instead of refusing",
                      "problem_type": general.problem_type})
        case_mod.update(conn, case_id, domain=general.domain,
                        problem_type=general.problem_type,
                        confidence=round(general.prior, 3), risk=general.risk,
                        title=general.label)
        # A question is still open above — answering it may add enough detail for a
        # later understand() call to match a real problem type and upgrade off this
        # path entirely. Until then, the case still moves rather than stalling.
        _move(conn, case_id, "INVESTIGATING",
              "no specific match; proceeding as a general consumer problem")
        return investigate(conn, case_id, use_llm=use_llm)

    top_definition = get_definition(top.problem_type)
    case_mod.update(conn, case_id, domain=top.domain, problem_type=top.problem_type,
                    confidence=round(top.posterior, 3),
                    risk=top_definition.risk if top_definition else "medium",
                    title=_title(c, top))

    if u.ambiguous:
        questions = understanding.rank_discriminators(u.hypotheses,
                                                      answered=set(answered), limit=2)
        for q in questions:
            case_mod.ask(conn, case_id, qid=f"{case_id}:{q['id']}",
                         question=q["question"], why=q["why"], kind=q["kind"],
                         options=q["options"], separates=q["separates"],
                         value_bits=q["expected_bits"])
        if questions:
            chain.append(conn, case_id, "questions.raised", "AGENT",
                         {"count": len(questions),
                          "reason": "several interpretations remain live",
                          "top_gain_bits": questions[0]["expected_bits"]})
            _move(conn, case_id, "NEEDS_INPUT",
                  f"{len([h for h in u.hypotheses if h.posterior > 0.1])} "
                  f"interpretations are still live")
            return snapshot(conn, case_id)

    _move(conn, case_id, "INVESTIGATING", f"reading this as {top.label.lower()}")
    return investigate(conn, case_id, use_llm=use_llm)


def _title(c: dict, top) -> str:
    return f"{top.label}"[:120]


def _answered_discriminators(conn, case_id: str) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute("SELECT id, answer FROM case_questions WHERE case_id = %s"
                    " AND status = 'ANSWERED' AND answer IS NOT NULL", (case_id,))
        rows = cur.fetchall()
    # Question ids are namespaced `<case>:<discriminator>` so the same discriminator
    # can be asked on many cases without colliding.
    return {r[0].split(":", 1)[-1]: r[1] for r in rows}


# ─────────────────────────────────────────────────────────────────────────────
# 3. evidence
# ─────────────────────────────────────────────────────────────────────────────
def attach(conn, case_id: str, *, kind: str, text: str,
           filename: str | None = None, raw: bytes | None = None,
           media_type: str | None = None, use_llm: bool = True,
           reanalyse: bool = True) -> dict:
    """Add one artefact, extract its facts, and look for contradictions."""
    _ready()
    c = case_mod.get(conn, case_id)
    if not c:
        raise ValueError(f"no such case {case_id}")
    if kind not in EVIDENCE_KINDS:
        raise ValueError(f"unknown evidence kind {kind!r}")

    ev = egraph.add_evidence(conn, case_id=case_id, workspace=c["workspace"],
                             subject=c["subject"], kind=kind, text=text, raw=raw,
                             filename=filename, media_type=media_type)

    definition = get_definition(c["problem_type"]) if c["problem_type"] else None
    want = tuple(definition.expected_facts) if definition else ()
    facts = extract.extract(text, kind, want=want, use_llm=use_llm)
    written = egraph.add_facts(conn, case_id, ev["id"], facts)
    found = contradiction.detect(conn, case_id)

    chain.append(conn, case_id, "evidence.added", "HUMAN",
                 {"kind": kind, "sha256": ev["sha256"], "bytes": ev["bytes"],
                  "evidence_id": ev["id"], "facts": len(written),
                  "counts": extract.gate_summary(facts),
                  "contradictions_found": len(found)},
                 seal=True, subject=c["subject"], workspace=c["workspace"])

    for f in found:
        chain.append(conn, case_id, "contradiction.detected", "AGENT",
                     {"severity": f["severity"], "predicate": f["predicate"],
                      "because": f["detail"]})

    result = {"evidence": ev, "facts": written, "contradictions": found}
    if reanalyse and c["state"] not in ("RESOLVED", "CLOSED_UNRESOLVED", "WITHDRAWN"):
        result["case"] = understand(conn, case_id, use_llm=use_llm)
    return result


def answer_question(conn, case_id: str, question_id: str, value: str, *,
                    use_llm: bool = True) -> dict:
    """Record an answer, store it as evidence, and re-run understanding.

    The answer becomes a `statement_note` evidence item so any fact derived from it
    still has a source. An agent that lets user answers float free of the evidence
    graph ends up making claims it cannot trace, which is the failure the whole
    evidence layer exists to prevent.
    """
    _ready()
    c = case_mod.get(conn, case_id)
    if not c:
        # Checked BEFORE case_mod.answer() runs. That call mutates the question
        # row unconditionally once question_id resolves, so calling it first and
        # discovering the case is missing only afterward — at the `c["workspace"]`
        # access below — left the question marked ANSWERED while everything past
        # it crashed with an unhandled TypeError. A caller error must not leave a
        # half-applied write behind.
        raise ValueError(f"no such case {case_id}")
    with conn.cursor() as cur:
        cur.execute("SELECT question FROM case_questions WHERE id = %s", (question_id,))
        row = cur.fetchone()
    q_text = row[0] if row else question_id

    case_mod.answer(conn, case_id, question_id, value)
    ev = egraph.add_evidence(conn, case_id=case_id, workspace=c["workspace"],
                             subject=c["subject"], kind="statement_note",
                             text=f"Q: {q_text}\nA: {value}",
                             filename="your-answer.txt", media_type="text/plain",
                             trust="user_capture")
    egraph.add_stated_fact(conn, case_id, f"answer.{question_id.split(':')[-1]}",
                           value, evidence_id=ev["id"], note=q_text)
    return understand(conn, case_id, use_llm=use_llm)


# ─────────────────────────────────────────────────────────────────────────────
# 4-6. investigate: gather, analyse, plan
# ─────────────────────────────────────────────────────────────────────────────
def investigate(conn, case_id: str, *, use_llm: bool = True) -> dict:
    """Gather what is retrievable, analyse rights, rank remedies, compose a plan."""
    _ready()
    c = case_mod.get(conn, case_id)
    if c is None:
        raise ValueError(f"case {case_id} not found")
    definition = get_definition(c["problem_type"]) if c["problem_type"] else None
    if definition is None:
        # Agent X's ontology does not model this problem, so there is no
        # entitlement to compute and the case correctly stops for input. But
        # "we cannot classify this" and "we have nothing for you" are different
        # answers, and this is where research earns its place: the corpus is
        # searched by the user's own words, not by a problem type, so it can
        # still surface the right complaint route for a dispute the catalogue
        # has never heard of. Nothing here classifies the case or unblocks it —
        # it attaches reading, and the state machine is untouched.
        sources = research.gather(conn, c)
        research.persist(conn, case_id, c["workspace"], sources)
        chain.append(conn, case_id, "research.gathered", "AGENT",
                     {**research.summary(sources), "unclassified": True})
        _move(conn, case_id, "NEEDS_INPUT", "no problem definition to work from")
        return snapshot(conn, case_id)

    counterparty = _counterparty(conn, case_id)

    # ── gather: retrieve what a provider can fetch ────────────────────────
    missing = _missing_evidence(conn, case_id, definition)
    retrieved = _retrieve_missing(conn, c, definition, missing, counterparty,
                                  use_llm=use_llm)
    if retrieved:
        missing = _missing_evidence(conn, case_id, definition)

    # ── facts, contradictions, jurisdiction ───────────────────────────────
    contradiction.detect(conn, case_id)
    blocking = contradiction.blocking(conn, case_id)
    base = egraph.fact_map(conn, case_id)
    facts = egraph.derived_facts(base)

    amount_minor, currency = _amount(conn, case_id, base)
    jurisdiction, why_j = policy.detect_jurisdiction(currency=currency)
    if jurisdiction:
        facts.setdefault("case.jurisdiction", jurisdiction)

    # ── policy ────────────────────────────────────────────────────────────
    findings = policy.analyse(definition, facts, jurisdiction)
    eligibility.persist_policies(conn, case_id, findings)
    chain.append(conn, case_id, "policy.analysed", "AGENT",
                 {"count": len(findings),
                  "applies": [f.policy.id for f in findings if f.applies == "yes"],
                  "unknown": [f.policy.id for f in findings if f.applies == "unknown"],
                  "jurisdiction": jurisdiction, "because": why_j})

    # ── research ──────────────────────────────────────────────────────────
    # Strictly downstream of the policy analysis above, and strictly incapable of
    # changing it: retrieval supplies the procedural detail (which ombudsman,
    # what deadline, what the published band is) that a correct entitlement still
    # needs to be actionable. It sets no fact and unlocks no action. Most cases
    # retrieve nothing, because the corpus covers five sectors and consumer
    # problems do not — that is recorded as honestly as a hit.
    sources = research.gather(conn, c, findings=findings)
    research.persist(conn, case_id, c["workspace"], sources)
    chain.append(conn, case_id, "research.gathered", "AGENT",
                 research.summary(sources))

    # ── deadlines ─────────────────────────────────────────────────────────
    _record_deadlines(conn, case_id, definition, findings, facts)

    # ── case confidence ───────────────────────────────────────────────────
    _set_case_confidence(conn, case_id, definition, missing, blocking)
    c = case_mod.get(conn, case_id)
    if c is None:
        raise ValueError(f"case {case_id} not found")

    # ── eligibility ───────────────────────────────────────────────────────
    remedies = eligibility.assess(definition=definition, findings=findings,
                                  facts=facts, missing_evidence=missing,
                                  blocking_contradictions=blocking,
                                  amount_minor=amount_minor, currency=currency)
    eligibility.persist(conn, case_id, remedies)
    case_mod.update(conn, case_id, amount_minor=amount_minor, currency=currency)
    chain.append(conn, case_id, "eligibility.assessed", "AGENT",
                 {"count": len(remedies),
                  "eligible": [r["kind"] for r in remedies if r["eligibility"] == "eligible"],
                  "blocked": [r["kind"] for r in remedies
                              if r["eligibility"] in ("needs_evidence", "ineligible")],
                  "because": eligibility.headline(remedies, amount_minor, currency)})

    # ── ask for what is still missing ─────────────────────────────────────
    asked = _ask_for_missing(conn, case_id, missing, blocking, findings)

    best = eligibility.best(remedies)
    if best is None:
        _move(conn, case_id, "NEEDS_INPUT" if asked else "INVESTIGATING",
              "no remedy is open on the evidence so far")
        return snapshot(conn, case_id)

    if (c.get("confidence") or 0) < MIN_CONFIDENCE_TO_PLAN:
        _move(conn, case_id, "NEEDS_INPUT",
              "confidence in the problem type is too low to plan against")
        return snapshot(conn, case_id)

    # ── plan ──────────────────────────────────────────────────────────────
    live = planner.active_plan(conn, case_id)
    if live and live.status == "ACTIVE":
        # Actions have already gone out under this plan. New evidence updates the
        # analysis and the remedy ranking, but it does not silently swap the plan
        # a case is mid-way through — that is a decision for the user, surfaced as
        # a re-plan option rather than taken behind their back.
        chain.append(conn, case_id, "plan.retained", "AGENT",
                     {"plan_id": live.id, "strategy": live.strategy,
                      "reason": "a plan is already in flight; the new analysis is "
                                "recorded but the running plan is not replaced",
                      "best_remedy_now": best["kind"]})
        return snapshot(conn, case_id)

    next_version = planner.supersede_drafts(conn, case_id)
    # The plan asks for what the REMEDY is worth, which is not always the amount
    # in dispute. A £185 fare that carries a £350 fixed statutory entitlement is a
    # £350 claim, and an approval card that says £185 understates what the user is
    # about to authorise Agent X to ask for.
    ask_minor = best.get("expected_value_minor") or amount_minor
    ask_currency = best.get("currency") or currency

    # What earlier cases against this same counterparty and problem type actually
    # produced. Advisory: it can reshape the wait and the chase budget of a plan
    # the policy layer already permitted, and nothing else — it cannot make a
    # remedy eligible or an action authorised.
    from agentx import outcomes
    prior = outcomes.prior_for(conn, workspace=c.get("workspace", "default"),
                               counterparty=counterparty,
                               problem_type=c["problem_type"],
                               mode=_dominant_mode(conn, case_id))
    if prior.get("cases"):
        chain.append(conn, case_id, "prior.consulted", "AGENT",
                     {"cases": prior["cases"], "actionable": prior["actionable"],
                      "because": prior["note"], "basis": prior["basis"]})

    plan = planner.compose(case=c, definition=definition, remedy=best["kind"], prior=prior,
                           findings=findings, missing_evidence=missing,
                           counterparty=counterparty, amount_minor=ask_minor,
                           currency=ask_currency,
                           deadlines=case_mod.deadlines(conn, case_id))
    note = "composed deterministically"
    if use_llm and plan.validation.get("ok"):
        plan, note = planner.propose_with_llm(plan, definition=definition,
                                              counterparty=counterparty)
    plan.version = next_version
    planner.persist(conn, plan)
    chain.append(conn, case_id, "plan.created", "AGENT",
                 {"plan_id": plan.id, "strategy": plan.strategy,
                  "steps": len(plan.steps), "confidence": plan.confidence,
                  "status": plan.status, "proposed_by": plan.proposed_by,
                  "because": note, "validation": plan.validation})

    if not plan.validation.get("ok"):
        _move(conn, case_id, "NEEDS_INPUT",
              f"the plan did not validate: {plan.validation['errors'][0]}")
        return snapshot(conn, case_id)

    _move(conn, case_id, "ACTION_REQUIRED",
          f"a validated {plan.strategy.replace('_', ' ')} plan is ready")
    return snapshot(conn, case_id)


def _set_case_confidence(conn, case_id: str, definition,
                         missing: list[dict], blocking: list[dict]) -> float:
    """How confident Agent X is IN THE CASE — not in its classification.

    These are different questions and conflating them was a real bug. The
    classifier's posterior answers "which problem is this?"; the governor needs
    "is this case solid enough to act on?", which also depends on whether the
    evidence the problem type requires is actually present and whether any of it
    is in dispute.

    A correctly classified case with every required document attached should clear
    an action floor even when a rival interpretation is still notionally alive —
    and a confidently classified case missing its critical evidence should not.
    Weighting the two halves evenly is what produces both behaviours.
    """
    interp = case_mod.interpretations(conn, case_id)
    posterior = float(interp[0]["posterior"]) if interp else 0.0

    critical = [r for r in definition.required_evidence if r.critical]
    missing_critical = [m for m in missing if m.get("critical")]
    completeness = (1.0 if not critical
                    else max(0.0, 1.0 - len(missing_critical) / len(critical)))

    composite = 0.5 * posterior + 0.5 * completeness
    if blocking:
        # A contested value is not a small deduction. Nothing that depends on it
        # can be trusted, and the governor has its own hard stop for this — the
        # halving here keeps the number the user sees consistent with the refusal.
        composite *= 0.5
    composite = round(max(0.02, min(0.99, composite)), 3)

    case_mod.update(conn, case_id, confidence=composite)
    chain.append(conn, case_id, "confidence.assessed", "AGENT",
                 {"confidence": composite,
                  "posterior": round(posterior, 3),
                  "because": (f"classification {posterior:.2f}, evidence "
                              f"completeness {completeness:.2f}"
                              + (f", {len(blocking)} blocking contradiction(s)"
                                 if blocking else ""))})
    return composite


def _missing_evidence(conn, case_id: str, definition) -> list[dict]:
    have = set(egraph.evidence_kinds(conn, case_id))
    out = []
    for req in definition.required_evidence:
        if any(req.accepts(k) for k in have):
            continue
        out.append({"kind": req.kind, "why": req.why, "critical": req.critical,
                    "satisfied_by": list(req.satisfied_by),
                    "label": EVIDENCE_KINDS.get(req.kind, {}).get("label", req.kind)})
    return out


def _retrieve_missing(conn, c: dict, definition, missing: list[dict],
                      counterparty: str | None, *, use_llm: bool) -> list[dict]:
    """Fetch what a provider can supply. Never invents what it cannot.

    Only runs for evidence kinds a provider family genuinely serves, and only when
    a reference exists to look up. A missing receipt cannot be retrieved from
    anywhere, so it stays missing and becomes an upload request.
    """
    got = []
    if not counterparty:
        return got
    for m in missing:
        if m["kind"] not in ("booking_confirmation", "cancellation_notice",
                             "boarding_pass", "order_confirmation", "invoice",
                             "provider_record", "tracking"):
            continue
        ref = _reference_for(conn, c["id"], m["kind"])
        cap = capabilities.get("booking_inspection")
        avail = capabilities.available("booking_inspection", provider_hint=counterparty)
        if not avail.get("available"):
            continue
        result = runner.run(conn, case=c, action="retrieve",
                            params={"counterparty": counterparty,
                                    "record_kind": planner._record_kind(m["kind"]),
                                    "reference": ref, "case_id": c["id"]},
                            capability=cap)
        if result.get("evidence_id"):
            text = egraph.evidence_text(conn, result["evidence_id"]) or ""
            facts = extract.extract(text, "provider_record",
                                    want=tuple(definition.expected_facts),
                                    use_llm=use_llm)
            egraph.add_facts(conn, c["id"], result["evidence_id"], facts)
            got.append({"kind": m["kind"], "evidence_id": result["evidence_id"],
                        "facts": len(facts)})
    return got


def _reference_for(conn, case_id: str, evidence_kind: str) -> str | None:
    prefer = {"booking_confirmation": "booking", "cancellation_notice": "booking",
              "boarding_pass": "booking", "order_confirmation": "order",
              "tracking": "shipment", "invoice": "account"}.get(evidence_kind, "order")
    ent = case_mod.entity(conn, case_id, prefer)
    if ent:
        return ent["value"]
    for k in ("booking", "order", "shipment", "account", "case_ref"):
        ent = case_mod.entity(conn, case_id, k)
        if ent:
            return ent["value"]
    return None


def _amount(conn, case_id: str, facts: dict) -> tuple[int | None, str | None]:
    """The amount in dispute, in minor units, with its currency.

    Prefers a charge over an invoice total over a quoted price, because the money
    that actually left the user's account is the money a remedy is measured in.
    """
    for pred in ("charge.amount", "invoice.total", "order.total", "booking.rate",
                 "quoted.amount", "stated.amount"):
        rows = egraph.facts_for(conn, case_id, pred)
        rows = [r for r in rows if r["value_num"] is not None and r["status"] != "SUPERSEDED"]
        if rows:
            best = max(rows, key=lambda r: r["confidence"] or 0)
            return int(best["value_num"]), best["unit"]
    ent = case_mod.entity(conn, case_id, "amount")
    if ent and ent.get("normalized") and ":" in ent["normalized"]:
        minor, cur = ent["normalized"].split(":", 1)
        try:
            return int(minor), (None if cur == "?" else cur)
        except ValueError:
            pass
    return None, None


def _record_deadlines(conn, case_id: str, definition, findings, facts) -> None:
    incident = facts.get("incident.date") or ids.now()
    for rule in definition.deadlines:
        case_mod.add_deadline(conn, case_id, kind=rule.kind, label=rule.label,
                              due_at=ids.in_days(rule.days, frm=str(incident)),
                              source=rule.source or definition.problem_type)
    for f in findings:
        if f.applies == "yes" and f.policy.window_days:
            case_mod.add_deadline(
                conn, case_id, kind="statutory" if f.policy.authority == "statute" else "scheme",
                label=f"{f.policy.title} — window closes",
                due_at=ids.in_days(f.policy.window_days, frm=str(incident)),
                source=f.policy.id)


def _ask_for_missing(conn, case_id: str, missing: list[dict],
                     blocking: list[dict], findings: list) -> int:
    """Turn each blocker into one specific, answerable request."""
    n = 0
    for m in missing:
        if not m["critical"]:
            continue
        case_mod.ask(conn, case_id, qid=f"{case_id}:need:{m['kind']}",
                     question=f"Can you upload the {m['label'].lower()}?",
                     why=m["why"], kind="evidence",
                     options=[m["label"]] + [EVIDENCE_KINDS.get(a, {}).get("label", a)
                                             for a in m["satisfied_by"]])
        n += 1
    for b in blocking:
        case_mod.ask(conn, case_id, qid=f"{case_id}:contradiction:{b['id']}",
                     question=f"Two of your documents disagree — {b['detail']}. "
                              f"Which is right?",
                     why="Agent X will not act on a value two sources disagree about.",
                     kind="choice", options=["The first one", "The second one",
                                             "I am not sure"])
        n += 1
    for f in findings:
        if f.applies == "unknown" and "case.jurisdiction" in (f.unknown_facts or []):
            case_mod.ask(conn, case_id, qid=f"{case_id}:jurisdiction",
                         question="Which country's consumer rules apply to this purchase?",
                         why="Consumer law is territorial, and the same facts give "
                             "different rights in different countries.",
                         kind="choice",
                         options=["United Kingdom", "European Union", "United States",
                                  "India", "Somewhere else"])
            n += 1
            break
    return n


# ─────────────────────────────────────────────────────────────────────────────
# 7-10. execute, wait, verify
# ─────────────────────────────────────────────────────────────────────────────
def advance(conn, case_id: str, *, max_steps: int = 6,
            as_of: str | None = None) -> dict:
    """Run the plan as far as authority and the external world allow.

    Stops — deliberately and visibly — at the first step needing an authorisation
    the case does not have. The pending approval is already recorded by the runner,
    so the UI has an approval card and the user has a decision to make, rather than
    a silent stall.
    """
    _ready()
    c = case_mod.get(conn, case_id)
    # A resolved case is finished. Without this guard a sweep that resolved a case
    # could still walk the rest of its plan and ask the user to approve an
    # escalation for something already refunded.
    if c and c["state"] in ("RESOLVED", "CLOSED_UNRESOLVED", "WITHDRAWN"):
        return {"case_id": case_id, "ran": [],
                "blocked": f"case is {c['state']}",
                "case": snapshot(conn, case_id)}
    plan = planner.active_plan(conn, case_id)
    if not plan:
        return {"case_id": case_id, "ran": [], "blocked": "no active plan"}
    if plan.status == "VALIDATED":
        assert plan.id is not None, "an active plan is always persisted"
        planner.set_plan_status(conn, plan.id, "ACTIVE")
        plan.status = "ACTIVE"

    counterparty = _counterparty(conn, case_id)
    blocking = len(contradiction.blocking(conn, case_id))
    ran: list[dict] = []

    for _ in range(max_steps):
        c = case_mod.get(conn, case_id)
        if c is None:
            break
        step = planner.next_step(plan)
        if step is None:
            break
        # While the ball is in the counterparty's court, the plan does not advance.
        # Chasing, verifying and escalating are all things that become correct at a
        # TIME, and the follow-up scheduler owns time. Without this guard the
        # executor ran the chase step the instant the request was submitted and
        # told the user their claim was "still within its stated response time" —
        # true, useless, and a chase wasted.
        if c["state"] == "WAITING_EXTERNAL":
            ran.append({"step": step.key, "action": step.action,
                        "blocked": "waiting_on_counterparty",
                        "message": "the counterparty has not reached its stated "
                                   "response time; the follow-up agent will chase "
                                   "when it does"})
            break
        cap = capabilities.get(step.capability) if step.capability else None
        params = {**step.params, "case_id": case_id,
                  "counterparty": step.params.get("counterparty") or counterparty,
                  "_blocking_contradictions": blocking}
        # Chasing, escalating and verifying all act on an EXISTING external case.
        # Without the reference the provider has nothing to look up and correctly
        # answers "not found" — a real failure that looks like a bug.
        if step.action in ("follow_up", "escalate", "verify"):
            last = _last_external_ref(conn, case_id)
            if last and not params.get("external_ref"):
                params["external_ref"] = last
        # The letter the draft step composed is what actually goes to the
        # counterparty. Without this the act step sent an empty body, the sandbox
        # company saw no citation, and the whole point of establishing entitlement
        # before writing was lost between two steps of the same plan.
        if step.action in ("request_refund", "submit_form", "email", "escalate"):
            body = _latest_draft(conn, case_id)
            if body and not params.get("body"):
                params["body"] = body

        if step.action == "draft":
            out = _do_draft(conn, c, step, params)
            ran.append(out)
            _finish_step(conn, plan, step, "DONE")
            continue

        if step.action == "schedule":
            out = _do_wait(conn, c, step, as_of=as_of)
            ran.append(out)
            _finish_step(conn, plan, step, "DONE")
            break                          # waiting is the point; stop here

        if step.action == "follow_up":
            # Chasing belongs to the follow-up agent, which knows the chase budget,
            # the backoff and when to give up and escalate. Running it from the plan
            # executor produced a chase with none of that bookkeeping, and the case
            # then walked past its own waiting state to a premature receipt.
            _finish_step(conn, plan, step, "DELEGATED")
            ran.append({"step": step.key, "action": step.action,
                        "blocked": "delegated_to_scheduler",
                        "message": "the follow-up agent will chase on the clock"})
            break

        if step.action == "verify" and step.capability == "resolution_record":
            from agentx import receipt as receipt_mod
            env = receipt_mod.issue(conn, case_id)
            ran.append({"step": step.key, "action": "issue_receipt",
                        "sha256": env.get("sha256"), "signed": env.get("signed")})
            _finish_step(conn, plan, step, "DONE")
            continue

        try:
            result = runner.run(conn, case=c, action=step.action, params=params,
                                step_id=step.id, capability=cap)
        except runner.NotAuthorized as e:
            _finish_step(conn, plan, step, "AWAITING_AUTH")
            _move(conn, case_id, "ACTION_REQUIRED",
                  f"{step.title} needs your approval")
            ran.append({"step": step.key, "action": step.action,
                        "blocked": "authorization_required", "prompt": e.prompt,
                        "rule": e.verdict.rule})
            return {"case_id": case_id, "ran": ran,
                    "blocked": "authorization_required",
                    "case": snapshot(conn, case_id)}

        ran.append({"step": step.key, "action": step.action,
                    "outcome": result.get("outcome"), "state": result.get("state"),
                    "external_ref": result.get("external_ref"),
                    "provider_mode": result.get("provider_mode"),
                    # A refusal carries its reason into the UI. A step that shows
                    # only "REFUSED" makes the governor look like a bug rather than
                    # the safety property it is.
                    "message": result.get("message") or result.get("error")})

        # A counterparty that takes the request "under review" has ACCEPTED it.
        # Treating pending as failure sent every case straight down its escalation
        # branch and skipped the wait it was supposed to observe — the opposite of
        # the escalation discipline the ladder exists to enforce.
        ok = result.get("state") == "COMPLETED" and result.get("outcome") in (
            "accepted", "done", "pending")

        if not ok and step.optional:
            # Enrichment that did not work. Recorded, skipped, and the plan carries
            # on: a merchant that does not publish its terms is not a reason to
            # abandon a claim that was otherwise ready to send.
            _finish_step(conn, plan, step, "SKIPPED")
            continue
        _finish_step(conn, plan, step, "DONE" if ok else "FAILED")
        _apply_branch(conn, plan, step, ok)
        _post_action(conn, case_id, step, result, as_of=as_of)

        if not ok and step.on_failure is None:
            break
        if result.get("responds_in_days"):
            break                          # the ball is in their court

    return {"case_id": case_id, "ran": ran, "case": snapshot(conn, case_id)}


def _latest_draft(conn, case_id: str) -> str | None:
    for cm in reversed(runner.communications(conn, case_id)):
        if cm["direction"] == "outbound" and cm["body"]:
            return cm["body"]
    return None


def _dominant_mode(conn, case_id: str) -> str | None:
    """Which world this case has been acting in, if it has acted at all.

    Priors are never pooled across sandbox and live: a lesson learned against a
    simulated company must not shape a plan against a real one. A case with no
    external action yet has no mode, and gets whatever history exists.
    """
    modes = {e.get("provider_mode") for e in runner.history(conn, case_id)
             if e.get("external_ref") and e.get("provider_mode")}
    return modes.pop() if len(modes) == 1 else None


def _last_external_ref(conn, case_id: str) -> str | None:
    for ex in reversed(runner.history(conn, case_id)):
        if ex["external_ref"] and ex["state"] == "COMPLETED" and ex["action"] != "retrieve":
            return ex["external_ref"]
    return None


def _finish_step(conn, plan, step, status: str) -> None:
    step.status = status
    assert step.id is not None, "a step reached from an active plan is always persisted"
    planner.set_step_status(conn, step.id, status)


def _apply_branch(conn, plan, step, ok: bool) -> None:
    """Walk the branch, marking the untaken path SKIPPED.

    Without this a plan that takes its failure branch deadlocks: the steps on the
    success path never run, and every later step waiting on them waits forever.
    """
    taken = step.on_success if ok else step.on_failure
    other = step.on_failure if ok else step.on_success
    if other and other != taken:
        target = plan.step(other)
        if target and target.status == "PENDING" and not _reachable(plan, taken, other):
            _finish_step(conn, plan, target, "SKIPPED")


def _reachable(plan, start: str | None, target: str) -> bool:
    seen, queue = set(), [start] if start else []
    while queue:
        k = queue.pop()
        if k == target:
            return True
        if k in seen:
            continue
        seen.add(k)
        s = plan.step(k)
        if s:
            queue += [x for x in (s.on_success, s.on_failure) if x]
    return False


def _do_draft(conn, c: dict, step, params: dict) -> dict:
    """Compose the message and record it. Nothing is sent."""
    from agentx import letters
    body, subject = letters.compose(conn, c, params)
    runner.record_communication(conn, c["id"], direction="outbound",
                                channel="draft", counterparty=params.get("counterparty"),
                                subject=subject, body=body)
    return {"step": step.key, "action": "draft", "subject": subject,
            "chars": len(body), "preview": body[:400]}


def _do_wait(conn, c: dict, step, *, as_of: str | None) -> dict:
    now = as_of or ids.now()
    days = int(step.wait_days or 5)
    case_mod.schedule_followup(conn, c["id"], kind="chase",
                               due_at=ids.in_days(days, frm=now),
                               step_id=step.id, require_state="WAITING_EXTERNAL",
                               max_attempts=2,
                               detail=f"no response after {days} days")
    _move(conn, c["id"], "WAITING_EXTERNAL",
          f"the counterparty has {days} days to respond")
    return {"step": step.key, "action": "wait", "days": days,
            "next_check": ids.in_days(days, frm=now)}


def _post_action(conn, case_id: str, step, result: dict, *, as_of: str | None) -> None:
    """State and scheduling consequences of one completed action."""
    now = as_of or ids.now()
    if step.action in ("request_refund", "submit_form", "email", "cancel", "escalate"):
        _move(conn, case_id, "ACTION_SUBMITTED",
              f"{step.title.lower()} — reference {result.get('external_ref') or 'pending'}")
        if result.get("outcome") == "refused":
            # They said no. There is nothing to wait for, and scheduling a chase
            # against a decided case wastes the chase budget the escalation ladder
            # depends on. The plan's failure branch takes it from here.
            _move(conn, case_id, "FOLLOW_UP_REQUIRED",
                  f"{result.get('message') or 'the counterparty refused'}"[:180])
            return
        if result.get("outcome") == "accepted":
            case_mod.schedule_followup(conn, case_id, kind="verify",
                                       due_at=ids.in_days(0.5, frm=now),
                                       step_id=step.id, max_attempts=3,
                                       detail="confirm it against their records")
        elif result.get("responds_in_days"):
            plan = planner.active_plan(conn, case_id)
            wait_step = plan.step("await_response") if plan else None
            chase_step = plan.step("chase") if plan else None
            case_mod.schedule_followup(
                conn, case_id, kind="chase",
                due_at=ids.in_days(result["responds_in_days"], frm=now),
                step_id=(chase_step.id if chase_step else step.id),
                require_state="WAITING_EXTERNAL", max_attempts=2,
                detail="their stated response time has elapsed")
            # The counterparty named its own response time, so the plan's generic
            # wait has been satisfied by something more specific. Leaving it PENDING
            # would make the next advance schedule a second, duplicate chase.
            if wait_step and wait_step.status == "PENDING":
                assert wait_step.id is not None, "a step on an active plan is always persisted"
                planner.set_step_status(conn, wait_step.id, "DONE")
            _move(conn, case_id, "WAITING_EXTERNAL",
                  f"they said they would respond within "
                  f"{result['responds_in_days']:.0f} days")
    if step.action == "verify":
        last = None
        for ex in reversed(runner.history(conn, case_id)):
            if ex["external_ref"] and ex["action"] != "verify":
                last = ex
                break
        if last:
            c = case_mod.get(conn, case_id)
            if c is None:
                return
            v = runner.verify(conn, case=c, execution_id=last["id"])
            if v["verified"] == "verified":
                case_mod.update(conn, case_id, resolution="resolved",
                                outcome_summary=v.get("detail"))
                _move(conn, case_id, "RESOLVED",
                      "confirmed against the counterparty's own records")


def approve(conn, case_id: str, authorization_id: str, *, granted: bool,
            by: str = "user", as_of: str | None = None) -> dict:
    """Record the user's decision and continue, or stand down."""
    _ready()
    with conn.cursor() as cur:
        cur.execute("SELECT step_id, action FROM authorizations WHERE id = %s",
                    (authorization_id,))
        row = cur.fetchone()
    step_id = row[0] if row else None

    runner.decide_authorization(conn, authorization_id, granted=granted, by=by)
    if not granted:
        _move(conn, case_id, "INVESTIGATING", "you declined this action")
        return snapshot(conn, case_id)

    plan = planner.active_plan(conn, case_id)
    if plan:
        # Only the authorised step is released. Releasing every AWAITING_AUTH step
        # would let one approval unlock a different action further down the plan —
        # in practice, approving an escalation ran a chase instead.
        for st in plan.steps:
            if st.status != "AWAITING_AUTH":
                continue
            if step_id and st.id != step_id:
                continue
            assert st.id is not None, "a step on an active plan is always persisted"
            planner.set_step_status(conn, st.id, "PENDING")
        # Everything the plan reached before the authorised step is behind us.
        if step_id:
            target = next((st for st in plan.steps if st.id == step_id), None)
            if target:
                for st in plan.steps:
                    if st.ordinal < target.ordinal and st.status == "PENDING":
                        assert st.id is not None, "a step on an active plan is always persisted"
                        planner.set_step_status(conn, st.id, "SKIPPED")

    # A paused follow-up is fired by the SCHEDULER, not the plan executor — it is
    # the only path that reschedules, counts the chase budget and escalates when
    # the budget runs out.
    from agentx import followup as _followup
    fired = _followup.run_due(conn, as_of=as_of, case_id=case_id)

    out = advance(conn, case_id, as_of=as_of)
    snap = out["case"]
    for f in fired:
        out.setdefault("ran", []).insert(0, {
            "step": "follow-up", "action": f.get("action"),
            "outcome": f.get("outcome"), "message": f.get("detail") or f.get("next")})
    # What the approval actually caused, carried back to the caller. Without it a
    # UI (or a demo transcript) shows the approval and then silence, because the
    # execution it unblocked happened inside this call.
    snap["ran"] = out.get("ran", [])
    return snap


def pending_approvals(conn, case_id: str) -> list[dict]:
    cols = ["id", "action", "prompt", "scope", "level", "requested_at", "step_id"]
    with conn.cursor() as cur:
        cur.execute("SELECT id, action, prompt, scope, level, requested_at, step_id"
                    " FROM authorizations WHERE case_id = %s AND granted IS NULL"
                    " ORDER BY requested_at ASC", (case_id,))
        return [dict(zip(cols, r)) for r in cur.fetchall()]


# The one attention-worthy fact each case state reduces to. Deliberately a
# strict subset of CASE_STATES, not a parallel vocabulary: adding a case state
# without deciding what it means for the action center is exactly the drift
# that leaves an item silently unsurfaced, so a state missing here is a state
# nobody has decided this about yet, not an oversight to route around.
_ACTION_KIND = {
    "ACTION_REQUIRED": "approval_required",
    "NEEDS_INPUT": "input_required",
    "FOLLOW_UP_REQUIRED": "follow_up_due",
    "ESCALATED": "follow_up_due",
    "RESOLVED": "resolved",
}


def action_items(conn, *, workspace: str = "default", user_ref: str | None = None,
                 limit: int = 100) -> list[dict]:
    """Everything across this workspace's cases that needs a person's attention,
    right now — the Action Center's one query.

    Deliberately derived from live case/approval/question state on every call
    rather than a separate notifications table: a case whose approval was
    already granted through the case page a moment ago cannot then show a
    stale "approval required" card here, because there is nothing to go stale
    — this reads the same rows the case detail page itself reads.
    """
    _ready()
    cases = case_mod.list_cases(conn, workspace=workspace, user_ref=user_ref,
                                state="open", limit=limit)
    # RESOLVED is terminal (excluded by state="open") but belongs in the Action
    # Center too — briefly, as the thing that just finished — so it is added
    # back explicitly rather than by loosening the open/closed filter, which
    # would also let CLOSED_UNRESOLVED and WITHDRAWN back in unwanted.
    cases += case_mod.list_cases(conn, workspace=workspace, user_ref=user_ref,
                                 state="RESOLVED", limit=limit)

    items: list[dict] = []
    for c in cases:
        kind = _ACTION_KIND.get(c["state"])
        if not kind:
            continue
        cid, title = c["id"], (c.get("title") or (c["description"] or "")[:80])
        if kind == "approval_required":
            for a in pending_approvals(conn, cid):
                items.append({"kind": kind, "case_id": cid, "title": title,
                             "detail": a["prompt"], "at": a["requested_at"],
                             "item_id": a["id"]})
        elif kind == "input_required":
            for q in case_mod.open_questions(conn, cid):
                items.append({"kind": kind, "case_id": cid, "title": title,
                             "detail": q["question"], "at": c["updated_at"],
                             "item_id": q["id"]})
        elif kind == "follow_up_due":
            items.append({"kind": kind, "case_id": cid, "title": title,
                         "detail": "waiting on a response that is now overdue",
                         "at": c["updated_at"], "item_id": cid})
        elif kind == "resolved":
            items.append({"kind": kind, "case_id": cid, "title": title,
                         "detail": c.get("outcome_summary") or "case resolved",
                         "at": c.get("closed_at") or c["updated_at"], "item_id": cid})

    items.sort(key=lambda i: i["at"] or "", reverse=True)
    return items[:limit]


# ─────────────────────────────────────────────────────────────────────────────
# the case view
# ─────────────────────────────────────────────────────────────────────────────
def snapshot(conn, case_id: str) -> dict:
    """Everything the UI needs about one case, in one call.

    One call rather than eight because a consumer screen that assembles a case from
    eight endpoints will show a half-updated case, and a half-updated case is one
    where an approval card and a state badge disagree.
    """
    _ready()
    c = case_mod.get(conn, case_id)
    if not c:
        raise ValueError(f"no such case {case_id}")

    remedies = eligibility.load(conn, case_id)
    executions = runner.history(conn, case_id)
    plan = planner.active_plan(conn, case_id)
    # The chain's HEAD, not a full re-walk. `snapshot()` is called on nearly every
    # engine operation, and `chain.verify()` re-hashes the whole chain from row 0
    # each time — so a case's read cost grew with its own history, quadratically
    # over a case's life. Integrity is still fully verifiable on demand at
    # GET /api/agentx/cases/{id}/chain, and every receipt re-verifies before it is
    # signed; a screen refresh does not need to re-prove the past to show a state.
    last_seq, head_hash = chain.head(conn, case_id)
    claims = _claims(conn, case_id, c)

    out = {
        "case": {**c, "state_copy": case_mod.state_copy(c["state"]),
                 "amount": normalize.fmt_money(c["amount_minor"], c["currency"])},
        "headline": eligibility.headline(remedies, c["amount_minor"], c["currency"]),
        "interpretations": case_mod.interpretations(conn, case_id),
        "entities": case_mod.entities(conn, case_id),
        "evidence": egraph.list_evidence(conn, case_id),
        "facts": egraph.facts_for(conn, case_id),
        "claims": [cl.as_dict() for cl in claims],
        "contradictions": contradiction.open_contradictions(conn, case_id),
        "policies": eligibility.load_policies(conn, case_id),
        "research": research.load(conn, case_id),
        "remedies": remedies,
        # Which of those remedies are genuinely different choices rather than
        # strictly worse ones. Additive: `remedies` and `headline` are unchanged,
        # and nothing here re-ranks anything.
        "tradeoffs": tradeoffs.analyse(remedies),
        "plan": plan.as_dict() if plan else None,
        "approvals": pending_approvals(conn, case_id),
        "executions": executions,
        "communications": runner.communications(conn, case_id),
        "deadlines": case_mod.deadlines(conn, case_id),
        "followups": case_mod.followups(conn, case_id),
        "questions": case_mod.open_questions(conn, case_id),
        "prior": (plan.prior if plan and plan.prior else None),
        "systemic": _systemic(conn, c),
        "chain": {"length": last_seq + 1, "head": head_hash,
                  "verify_url": f"/api/agentx/cases/{case_id}/chain"},
        "engine": store.describe(),
    }
    # Stage track + live alerts. Built from the assembled snapshot because it
    # reads the case's own deadlines, questions and approvals — it is a view over
    # what is already here, not another query.
    out["briefing"] = stages.briefing(out)
    return out


def _systemic(conn, c: dict) -> dict | None:
    """Whether this counterparty refuses this problem class as a matter of course.

    The one thing an individual complainant structurally cannot see: they only
    ever have their own case. Deliberately conservative thresholds — see
    `outcomes.systemic_signal`.
    """
    try:
        from agentx import outcomes
        return outcomes.systemic_signal(
            conn, workspace=c.get("workspace", "default"),
            counterparty=_counterparty(conn, c["id"]),
            problem_type=c.get("problem_type"))
    except Exception:
        return None


def _claims(conn, case_id: str, c: dict) -> list:
    """The statements Agent X is prepared to make, each traced to evidence."""
    definition = get_definition(c["problem_type"]) if c["problem_type"] else None
    contested = contradiction.contested_predicates(conn, case_id)
    wanted = list(definition.expected_facts) if definition else []
    out = []
    for pred in wanted[:6]:
        text = _claim_text(pred, conn, case_id, c)
        if not text:
            continue
        cl = egraph.build_claim(conn, case_id, pred, text, contested)
        if cl:
            out.append(cl)
    return out


def _claim_text(predicate: str, conn, case_id: str, c: dict) -> str | None:
    """The sentence Agent X would say about one predicate, or None if it has nothing.

    A dict of format strings would be evaluated for every predicate at once, so a
    money value would be run through the flight-delay formatter and raise. Each
    branch formats only its own value.
    """
    rows = egraph.facts_for(conn, case_id, predicate)
    if not rows:
        return None
    best = max(rows, key=lambda r: r["confidence"] or 0)
    val = best["value_text"]
    n = len(rows)

    if predicate == "charge.amount":
        return f"You were charged {val}" + (f", {n} separate times." if n > 1 else ".")
    if predicate == "charge.date":
        return f"The charge is dated {val}."
    if predicate == "charge.status":
        return f"The charge shows as {val}."
    if predicate in ("invoice.total", "order.total"):
        return f"The {'bill' if 'invoice' in predicate else 'order'} totals {val}."
    if predicate == "order.id":
        return f"The order reference is {val}."
    if predicate == "booking.reference":
        return f"The booking reference is {val}."
    if predicate == "booking.status":
        return f"The booking is now {val}."
    if predicate == "booking.rate":
        return f"The agreed rate was {val}."
    if predicate == "flight.number":
        return f"The flight is {val}."
    if predicate == "flight.delay_minutes":
        mins = best["value_num"]
        if mins is None:
            return None
        hours, rest = divmod(int(mins), 60)
        return f"The flight arrived {hours}h {rest:02d}m late."
    if predicate == "shipment.status":
        return f"The shipment shows as {val}."
    if predicate == "merchant.name":
        return f"The counterparty is {val}."
    if val is None:
        return None
    return f"{predicate.replace('.', ' ')}: {val}."


def _counterparty(conn, case_id: str) -> str | None:
    ent = (case_mod.entity(conn, case_id, "merchant")
           or case_mod.entity(conn, case_id, "provider"))
    return ent["value"] if ent else None


def _move(conn, case_id: str, to_state: str, why: str) -> None:
    try:
        case_mod.transition(conn, case_id, to_state, why=why)
    except case_mod.InvalidTransition as e:
        chain.append(conn, case_id, "case.state.refused", "SYSTEM",
                     {"to_state": to_state, "because": str(e)[:240]})


# ─────────────────────────────────────────────────────────────────────────────
# closing
# ─────────────────────────────────────────────────────────────────────────────
def close(conn, case_id: str, *, resolution: str, summary: str = "") -> dict:
    """Close a case and issue its receipt."""
    _ready()
    state = {"resolved": "RESOLVED", "unresolved": "CLOSED_UNRESOLVED",
             "withdrawn": "WITHDRAWN"}.get(resolution, "CLOSED_UNRESOLVED")
    case_mod.update(conn, case_id, resolution=resolution, outcome_summary=summary)
    _move(conn, case_id, state, summary or f"closed as {resolution}")
    from agentx import receipt as receipt_mod
    env = receipt_mod.issue(conn, case_id)
    return {"case_id": case_id, "state": state, "receipt_sha256": env.get("sha256"),
            "signed": env.get("signed")}


def evidence_package(conn, case_id: str, *, audience: str = "human_review") -> dict:
    """Build, sign and store an evidence package for a specific audience."""
    _ready()
    c = case_mod.get(conn, case_id)
    if c is None:
        raise ValueError(f"case {case_id} not found")
    body = pkg.build(conn, case_id, audience=audience, claims=_claims(conn, case_id, c))
    from agentx.receipt import _signing_key
    env = pkg.sign(body, _signing_key())
    pkg.store_package(conn, case_id, env)
    chain.append(conn, case_id, "evidence_package.issued", "SYSTEM",
                 {"audience": audience, "sha256": env.get("sha256"),
                  "signed": bool(env.get("signed")),
                  "count": len(body.get("evidence") or [])})
    return env
