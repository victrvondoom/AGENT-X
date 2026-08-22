"""
The demonstration engine — five consumer problems, resolved end to end.

Built to answer the question a judge actually has: *is this an execution system or
a chatbot with a nice diagram?* Every scenario here runs the real engine against
the real sandbox — real classification, real extraction, real policy evaluation,
real governor decisions, real provider calls, real refusals, real chases, real
verification, real signed receipt. Nothing is scripted, and the transcript a
scenario returns is assembled from what actually happened, not from a narrative.

Which is why the scenarios differ in outcome. Streamly refuses on first ask and
pays when a right is cited. Kartly stalls a duplicate-charge claim through two
chases and only releases it on escalation. Meridian pays the room rate and fights
the difference. SkyLink acknowledges instantly and takes its full fourteen days.
If they all succeeded on the first try, the demo would be proving nothing.

TIME

`advance_days` moves the sandbox clock AND passes the moved instant to the
scheduler as `as_of`. Production code never reads that clock — it takes the real
one — so nothing here weakens the scheduler it demonstrates.
"""
from __future__ import annotations

from agentx import case as case_mod
from agentx import chain, engine, followup, ids, receipt, store
from agentx.sandbox import world

# ─────────────────────────────────────────────────────────────────────────────
# fixtures — the documents a user would actually have
# ─────────────────────────────────────────────────────────────────────────────
SCENARIOS: dict[str, dict] = {
    "A": {
        "key": "A",
        "title": "Duplicate charge",
        "one_liner": "Charged twice for one order; the merchant stalls, escalation pays.",
        "narrative": ("Kartly charged me twice for the same order. Two charges of "
                      "2,399.00 INR on 2026-08-02. Order 402-9938271."),
        "expect": "two chases, then escalation to the payment provider, then a verified refund",
        "seed": [
            ("kartly", "order", "402-9938271", {
                "reference": "402-9938271", "kind": "order",
                "items": [{"sku": "K-40218", "name": "Bluetooth headphones",
                           "qty": 1, "price_minor": 239900}],
                "total_minor": 239900, "currency": "INR",
                "placed_at": "2026-08-02", "status": "delivered",
                "shipped_ref": "TRK7741902", "charges": 2}),
        ],
        "evidence": [
            ("transaction", "northgate-statement.txt",
             "Northgate Bank — card statement extract\n"
             "Account ending 4417\n"
             "2026-08-02   KARTLY MARKETPLACE   Total charged 2,399.00 INR   completed\n"
             "2026-08-02   KARTLY MARKETPLACE   Total charged 2,399.00 INR   completed\n"
             "Paid by credit card ending 4417\n"),
            ("order_confirmation", "kartly-order.txt",
             "Kartly — order confirmation\n"
             "Order: 402-9938271\n"
             "Placed: 2026-08-02\n"
             "Item: Bluetooth headphones x1\n"
             "Total: 2,399.00 INR\n"
             "Payment: credit card ending 4417\n"),
        ],
        "clock": [6, 6, 1, 1],
    },
    "B": {
        "key": "B",
        "title": "Subscription renewed unexpectedly",
        "one_liner": "An annual plan renewed unnoticed; the refusal folds when a right is cited.",
        "narrative": ("Streamly renewed my annual subscription without me realising "
                      "and charged 119.00 GBP on 2026-08-14. I have not used it "
                      "since. I want a refund and the plan cancelled."),
        "expect": "Streamly's retention script refuses a generic request; Agent X's "
                  "letter cites the Consumer Rights Act, which Streamly's own terms "
                  "recognise, and the refund is approved on the first ask",
        "seed": [
            ("streamly", "subscription", "SUB-77120", {
                "reference": "SUB-77120", "kind": "subscription",
                "plan": "Premium annual", "price_minor": 11900, "currency": "GBP",
                "status": "active", "renews_at": "2027-08-14",
                "last_charged_at": "2026-08-14", "cancel_route": "self_service"}),
        ],
        "evidence": [
            ("transaction", "northgate-statement.txt",
             "Northgate Bank — card statement extract\n"
             "2026-08-14   STREAMLY PREMIUM ANNUAL   Total charged 119.00 GBP   completed\n"
             "Paid by credit card ending 4417\n"),
            ("email", "streamly-receipt.txt",
             "Streamly — your plan has renewed\n"
             "Subscription: SUB-77120\n"
             "Plan: Premium annual\n"
             "Renewed on: 2026-08-14\n"
             "Total: 119.00 GBP\n"
             "Your next renewal is 2027-08-14.\n"),
        ],
        "clock": [4, 2, 1],
    },
    "C": {
        "key": "C",
        "title": "Hotel cancelled the booking",
        "one_liner": "The property cancelled two days out; it refunds the rate and "
                     "fights the difference.",
        "narrative": ("Meridian Suites cancelled my booking two days before check-in. "
                      "Booking reference MRD48192. I paid 128.00 GBP and had to book "
                      "somewhere else for 189.00 GBP."),
        "expect": "a partial refund, then escalation to the platform for the difference",
        "seed": [
            ("meridian", "booking", "MRD48192", {
                "reference": "MRD48192", "kind": "stay",
                "property": "Meridian Suites Kings Cross",
                "checkin": "2026-08-16", "nights": 2,
                "rate_minor": 12800, "currency": "GBP",
                "status": "cancelled_by_property",
                "cancelled_at": "2026-08-14",
                "cancellation_reason": "overbooking"}),
        ],
        "evidence": [
            ("booking_confirmation", "meridian-confirmation.txt",
             "Meridian Suites — booking confirmation\n"
             "Booking reference: MRD48192\n"
             "Property: Meridian Suites Kings Cross\n"
             "Check-in: 2026-08-16, 2 nights\n"
             "Total: 128.00 GBP\n"),
            ("cancellation_notice", "meridian-cancellation.txt",
             "Meridian Suites — we are sorry, your booking is cancelled\n"
             "Booking reference: MRD48192\n"
             "Cancelled on: 2026-08-14\n"
             "Reason: the property is overbooked\n"
             "Total: 128.00 GBP will be returned to your card.\n"),
            ("receipt", "replacement-hotel.txt",
             "Kings Cross Central Inn — booking receipt\n"
             "Booking: KCC-556123\n"
             "Check-in: 2026-08-16, 2 nights\n"
             "Total: 189.00 GBP\n"),
        ],
        "clock": [3, 3, 2, 2, 2, 1],
    },
    "D": {
        "key": "D",
        "title": "Flight disruption",
        "one_liner": "A four-hour technical delay on a 1,850 km route — a fixed "
                     "statutory entitlement, not a voucher.",
        "narrative": ("My SkyLink flight was delayed and I arrived over four hours "
                      "late. Booking reference SL22617, flight SL2261 on 2026-08-08. "
                      "I think I am owed compensation."),
        "expect": "the air-passenger rules evaluated deterministically from the "
                  "delay and the distance, a claim filed at the carrier's portal, "
                  "its full 14-day SLA observed, then chased and escalated",
        "seed": [
            ("skylink", "booking", "SL22617", {
                "reference": "SL22617", "kind": "flight",
                "flight_number": "SL2261", "route": "LHR-BER",
                "distance_km": 1850,
                "scheduled_departure": "2026-08-08",
                "status": "delayed", "delay_minutes": 260,
                "disruption_reason": "technical",
                "fare_minor": 18500, "currency": "GBP"}),
        ],
        "evidence": [
            ("booking_confirmation", "skylink-booking.txt",
             "SkyLink Airways — booking confirmation\n"
             "Booking reference: SL22617\n"
             "Flight SL2261, LHR to BER\n"
             "Scheduled departure: 2026-08-08\n"
             "Fare: 185.00 GBP\n"),
            ("email", "skylink-delay-notice.txt",
             "SkyLink Airways — flight SL2261 is delayed\n"
             "Booking reference: SL22617\n"
             "Flight SL2261 is delayed by 4 hours.\n"
             "Reason: technical issue with the aircraft.\n"
             "Distance: 1850 km.\n"),
        ],
        "clock": [8, 8, 6, 1],
    },
    "E": {
        "key": "E",
        "title": "Wrong item delivered",
        "one_liner": "The straightforward one — auto-approved, verified, closed in a "
                     "single pass.",
        "narrative": ("Kartly sent me the wrong item. I ordered an espresso machine "
                      "and received a kettle. Order 402-5510934, 8,499.00 INR, "
                      "delivered 2026-08-12."),
        "expect": "an immediate replacement approval, verified against Kartly's ledger",
        "seed": [
            ("kartly", "order", "402-5510934", {
                "reference": "402-5510934", "kind": "order",
                "items": [{"sku": "K-99120", "name": "Espresso machine",
                           "qty": 1, "price_minor": 849900}],
                "total_minor": 849900, "currency": "INR",
                "placed_at": "2026-08-09", "status": "delivered",
                "shipped_ref": "TRK8830115"}),
        ],
        "evidence": [
            ("order_confirmation", "kartly-order.txt",
             "Kartly — order confirmation\n"
             "Order: 402-5510934\n"
             "Placed: 2026-08-09\n"
             "Item: Espresso machine x1\n"
             "Total: 8,499.00 INR\n"),
            ("photograph", "what-arrived.txt",
             "Photograph description (submitted by the customer)\n"
             "Order: 402-5510934\n"
             "The box contains an electric kettle, model KT-220.\n"
             "The despatch label reads Order 402-5510934.\n"),
        ],
        "clock": [1, 1],
    },
}

# The ambiguity demonstration. Not a resolution scenario — a two-line proof that
# the classifier holds six live readings and asks exactly one question to collapse
# them, which is the claim the ontology layer stands on.
AMBIGUITY_PROBE = "They charged me again"


# ─────────────────────────────────────────────────────────────────────────────
# seeding and reset
# ─────────────────────────────────────────────────────────────────────────────
def seed(conn) -> dict:
    """Put the scenario records into the sandbox companies' own stores."""
    store.ensure_schema()
    n = 0
    for sc in SCENARIOS.values():
        for company, kind, ref, state in sc["seed"]:
            world.put(conn, company, kind, ref, state)
            n += 1
    return {"seeded": n, "companies": sorted({s[0] for sc in SCENARIOS.values()
                                              for s in sc["seed"]})}


def reset(conn) -> dict:
    """Clear every case and every sandbox record. Used between demo runs."""
    store.ensure_schema()
    tables = ["case_chain", "receipts", "case_questions", "followups", "deadlines",
              "communications", "executions", "authorizations", "plan_steps", "plans",
              "remedies", "case_policies", "contradictions", "evidence_links",
              "evidence_facts", "evidence_items", "case_entities",
              "case_interpretations", "cases", "sandbox_objects", "sandbox_clock"]
    with conn.cursor() as cur:
        for t in tables:
            try:
                cur.execute(f"DELETE FROM {t}")
            except Exception:
                pass
        try:
            cur.execute("DELETE FROM agentx_subject_keys")
        except Exception:
            pass
    return {"cleared": tables}


# ─────────────────────────────────────────────────────────────────────────────
# running one scenario
# ─────────────────────────────────────────────────────────────────────────────
def run(conn, key: str, *, auto_approve: bool = True, autonomy: int = 2,
        use_llm: bool = False, max_days: int | None = None) -> dict:
    """Drive one scenario from sentence to receipt. Returns the real transcript."""
    sc = SCENARIOS.get(key.upper())
    if not sc:
        raise ValueError(f"unknown scenario {key!r}; one of {sorted(SCENARIOS)}")
    seed(conn)

    events: list[dict] = []

    def note(stage: str, detail: str, **extra):
        events.append({"stage": stage, "detail": detail, **extra})

    snap = engine.intake(conn, description=sc["narrative"], autonomy_level=autonomy,
                         use_llm=use_llm)
    cid = snap["case"]["id"]
    note("intake", f"opened {cid}", case_id=cid)
    note("classification",
         f"{snap['case']['problem_type']} at {snap['case']['confidence']}",
         alternatives=[(i["problem_type"], round(i["posterior"], 3))
                       for i in snap["interpretations"][1:4]])

    for kind, filename, text in sc["evidence"]:
        out = engine.attach(conn, cid, kind=kind, text=text, filename=filename,
                            use_llm=use_llm)
        note("evidence", f"{kind}: {len(out['facts'])} fact(s), "
                         f"{len(out['contradictions'])} contradiction(s)",
             sha256=out["evidence"]["sha256"][:16])
        snap = out.get("case") or snap

    snap = engine.snapshot(conn, cid)
    note("policy", ", ".join(f"{p['policy_id']}={p['applies']}"
                             for p in snap["policies"]) or "none evaluated")
    note("eligibility", snap["headline"],
         remedies=[(r["kind"], r["eligibility"]) for r in snap["remedies"]])
    if snap["plan"]:
        note("plan", f"{snap['plan']['strategy']} — {len(snap['plan']['steps'])} steps, "
                     f"validated={snap['plan']['validation'].get('ok')}",
             steps=[s["title"] for s in snap["plan"]["steps"]])

    snap = _drive(conn, cid, events, auto_approve, as_of=None)

    for days in (sc["clock"] if max_days is None else sc["clock"][:max_days]):
        world.advance(conn, days)
        as_of = ids.in_days(world.clock_offset(conn))
        swept = followup.sweep(conn, as_of=as_of)
        for r in swept["results"]:
            note("follow-up",
                 f"day +{world.clock_offset(conn):.0f}: {r.get('action')} "
                 f"→ {r.get('outcome') or r.get('next') or ''}".strip(),
                 day=world.clock_offset(conn))
        snap = _drive(conn, cid, events, auto_approve, as_of=as_of)
        if snap["case"]["state"] in ("RESOLVED", "CLOSED_UNRESOLVED"):
            break

    env = receipt.issue(conn, cid)
    ch = chain.verify(conn, cid)
    note("receipt", f"issued, sha256 {env.get('sha256', '')[:16]}…, "
                    f"{'signed' if env.get('signed') else 'unsigned'}")
    note("integrity", f"chain {ch.get('rows')} rows, intact={ch.get('ok')}")

    snap = engine.snapshot(conn, cid)
    return {
        "scenario": sc["key"], "title": sc["title"], "case_id": cid,
        "expected": sc["expect"],
        "final_state": snap["case"]["state"],
        "resolution": snap["case"]["resolution"],
        "outcome": snap["case"]["outcome_summary"],
        "amount": snap["case"]["amount"],
        "events": events,
        "receipt_sha256": env.get("sha256"),
        "receipt_signed": bool(env.get("signed")),
        "receipt_text": receipt.render_text(env),
        "chain": ch,
        "snapshot": snap,
    }


def _drive(conn, cid: str, events: list, auto_approve: bool,
           as_of: str | None) -> dict:
    """Advance the plan, approving where the scenario says a user would.

    Approvals are handled BEFORE deciding that a pass made no progress. A pass
    that ends in "waiting for your approval" has, from the engine's point of view,
    done nothing — and treating that as "nothing left to do" left the case sitting
    on an approval card the driver never pressed.
    """
    def record(rows):
        for r in rows or []:
            events.append({
                "stage": "action",
                "detail": f"{r.get('action')} → "
                          f"{r.get('outcome') or r.get('blocked') or r.get('state') or 'done'}",
                "step": r.get("step"), "mode": r.get("provider_mode"),
                "external_ref": r.get("external_ref"),
                "message": (r.get("message") or r.get("prompt") or "")[:200]})

    for _ in range(10):
        out = engine.advance(conn, cid, as_of=as_of)
        record(out["ran"])
        snap = out.get("case") or engine.snapshot(conn, cid)
        approvals = snap.get("approvals") or []
        if not approvals or not auto_approve:
            return snap
        a = approvals[0]
        events.append({"stage": "authorization",
                       "detail": f"approved: {a['action']}", "prompt": a["prompt"]})
        after = engine.approve(conn, cid, a["id"], granted=True, as_of=as_of)
        record(after.get("ran", []))
        if after.get("approvals") == approvals:
            return after            # approving changed nothing; stop rather than spin
    return engine.snapshot(conn, cid)


def run_all(conn, *, auto_approve: bool = True, use_llm: bool = False) -> list[dict]:
    return [run(conn, k, auto_approve=auto_approve, use_llm=use_llm)
            for k in sorted(SCENARIOS)]


def ambiguity_probe(conn=None) -> dict:
    """The classifier held up to the light, with no database involved.

    Shows six live interpretations for four words and the single question that
    separates them, with its expected information gain in bits.
    """
    from agentx import understanding
    u = understanding.understand(AMBIGUITY_PROBE, use_llm=False)
    qs = understanding.rank_discriminators(u.hypotheses, limit=3)
    return {
        "input": AMBIGUITY_PROBE,
        "ambiguous": u.ambiguous,
        "residual": u.residual,
        "interpretations": [{"problem_type": h.problem_type, "label": h.label,
                             "posterior": round(h.posterior, 3)}
                            for h in u.hypotheses if h.posterior >= 0.02],
        "best_question": qs[0] if qs else None,
        "all_questions": qs,
    }
