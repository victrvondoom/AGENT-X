"""
The sandbox world — five companies that behave like companies.

This exists because a demonstration that depends on live third-party websites is a
demonstration that fails at the worst possible moment, and because the alternative
usually offered — a button that prints "Refund successful!" — proves nothing at
all. Neither is acceptable for a system whose entire claim is that it executes and
verifies.

So the sandbox is built to the standard the rest of the product is held to:

  * **State persists.** A subscription you cancel stays cancelled. A refund
    approved on day 7 is still approved on day 8. The companies keep records in
    `sandbox_objects`, and a judge can query that table directly and compare it to
    what Agent X's case file claims.

  * **They say no.** Kartly's first refund request goes "under review" for five
    business days. Streamly refuses a renewal refund on the first ask and only
    relents when a specific right is cited. Meridian pays the room rate and
    refuses the difference until the platform is brought in. A system that only
    ever succeeds has never been tested.

  * **They are slow.** Every response carries a `responds_in_days`, which is what
    puts a case into WAITING_EXTERNAL and what the follow-up agent later wakes up
    against. The sandbox clock can be advanced so a seven-day chase takes a
    second, and advancing it is an explicit demo action, never something
    production code can do.

  * **They are deterministic.** Behaviour is seeded from the reference, so the
    same case produces the same story every run — but it is not scripted: the
    outcome depends on what Agent X actually sends, including whether the message
    cites a right the company recognises.

  * **They are labelled.** Every result carries `mode="sandbox"` all the way to
    the receipt. Nothing here is ever presented as a real-world action.
"""
from __future__ import annotations

import hashlib
import json
import re

from agentx import ids, store

# ─────────────────────────────────────────────────────────────────────────────
# the companies
# ─────────────────────────────────────────────────────────────────────────────
COMPANIES: dict[str, dict] = {
    "skylink": {
        "name": "SkyLink Airways", "family": "booking", "domain": "travel",
        "support_email": "claims@skylink.example",
        "portal": "https://skylink.example/claims",
        "sla_days": 14,
        "recognises": ["eu261", "uk261", "montreal_convention"],
        "blurb": "A mid-size carrier with a formal claims portal and a 14-day SLA.",
    },
    "meridian": {
        "name": "Meridian Suites", "family": "booking", "domain": "hospitality",
        "support_email": "guestcare@meridiansuites.example",
        "portal": "https://meridiansuites.example/help",
        "sla_days": 5,
        "recognises": ["uk_cra_2015", "merchant_terms"],
        "blurb": "A hotel group that refunds the room rate readily and resists "
                 "paying the difference on a replacement booking.",
    },
    "kartly": {
        "name": "Kartly", "family": "merchant", "domain": "commerce",
        "support_email": "resolution@kartly.example",
        "portal": "https://kartly.example/orders",
        "sla_days": 5,
        "recognises": ["uk_cra_2015", "uk_ccr_2013", "us_ftc_mito",
                       "in_consumer_protection_2019"],
        "blurb": "A large marketplace. Fast on wrong-item replacements, slow and "
                 "procedural on duplicate charges.",
    },
    "streamly": {
        "name": "Streamly", "family": "subscription", "domain": "subscriptions",
        "support_email": "billing@streamly.example",
        "portal": "https://streamly.example/account/plan",
        "sla_days": 3,
        "recognises": ["us_rosca_click_to_cancel", "uk_ccr_2013", "uk_cra_2015"],
        "blurb": "A streaming service with a working cancel button and a "
                 "retention script that refuses renewal refunds on first ask.",
    },
    "nimbus": {
        "name": "Nimbus Mobile", "family": "telecom", "domain": "telecom",
        "support_email": "care@nimbusmobile.example",
        "portal": "https://nimbusmobile.example/billing",
        "sla_days": 7,
        "recognises": ["uk_ofcom_gc", "uk_cra_2015"],
        "blurb": "A mobile network. Will itemise a bill on request and credits "
                 "genuine errors without argument.",
    },
}

ALIASES = {
    "skylink airways": "skylink", "skylink": "skylink", "sl": "skylink",
    "meridian suites": "meridian", "meridian": "meridian",
    "kartly": "kartly", "streamly": "streamly",
    "nimbus mobile": "nimbus", "nimbus": "nimbus",
}


def resolve_company(name: str | None) -> str | None:
    """Map a merchant name to a sandbox company, or None if it is not one of ours.

    Returning None matters: Agent X must be able to say "I have no provider for
    Acme Ltd" rather than quietly resolving every unknown merchant to a sandbox
    company and producing a fake success.
    """
    if not name:
        return None
    n = str(name).strip().lower()
    if n in ALIASES:
        return ALIASES[n]
    for alias, cid in ALIASES.items():
        if alias in n:
            return cid
    return None


# ─────────────────────────────────────────────────────────────────────────────
# clock
# ─────────────────────────────────────────────────────────────────────────────
def clock_offset(conn) -> float:
    with conn.cursor() as cur:
        cur.execute("SELECT offset_days FROM sandbox_clock WHERE id = 'default'")
        row = cur.fetchone()
    return float(row[0]) if row else 0.0


def advance(conn, days: float) -> dict:
    """Move the sandbox world forward. A demo action, never a production one."""
    cur_off = clock_offset(conn) + float(days)
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM sandbox_clock WHERE id = 'default'")
        if cur.fetchone():
            cur.execute("UPDATE sandbox_clock SET offset_days = %s, updated_at = %s"
                        " WHERE id = 'default'", (cur_off, ids.now()))
        else:
            cur.execute("INSERT INTO sandbox_clock (id, offset_days, updated_at)"
                        " VALUES ('default', %s, %s)", (cur_off, ids.now()))
    return {"offset_days": cur_off, "sandbox_now": now(conn)}


def reset_clock(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM sandbox_clock WHERE id = 'default'")


def now(conn) -> str:
    return ids.in_days(clock_offset(conn))


# ─────────────────────────────────────────────────────────────────────────────
# object store
# ─────────────────────────────────────────────────────────────────────────────
def _key(company: str, kind: str, ref: str) -> str:
    return f"{company}:{kind}:{str(ref).strip().upper()}"


def put(conn, company: str, kind: str, ref: str, state: dict) -> dict:
    k = _key(company, kind, ref)
    blob = store.jdump(state)
    ts = ids.now()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM sandbox_objects WHERE id = %s", (k,))
        if cur.fetchone():
            cur.execute("UPDATE sandbox_objects SET state = %s, updated_at = %s"
                        " WHERE id = %s", (blob, ts, k))
        else:
            cur.execute(
                "INSERT INTO sandbox_objects (id, company, kind, reference, state,"
                " created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (k, company, kind, str(ref).upper(), blob, ts, ts))
    return state


def fetch(conn, company: str, kind: str, ref: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("SELECT state FROM sandbox_objects WHERE id = %s",
                    (_key(company, kind, ref),))
        row = cur.fetchone()
    return store.jload(row[0], None) if row else None


def all_objects(conn, company: str | None = None) -> list[dict]:
    sql = ("SELECT id, company, kind, reference, state, updated_at FROM sandbox_objects")
    params: list = []
    if company:
        sql += " WHERE company = %s"
        params.append(company)
    sql += " ORDER BY company, kind, reference"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [{"id": r[0], "company": r[1], "kind": r[2], "reference": r[3],
                 "state": store.jload(r[4], {}), "updated_at": r[5]}
                for r in cur.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# deterministic generation
# ─────────────────────────────────────────────────────────────────────────────
def _seed(*parts: str) -> int:
    return int(hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:8], 16)


def _pick(seed: int, options: list):
    return options[seed % len(options)]


def generate(conn, company: str, kind: str, ref: str) -> dict:
    """Invent a plausible record for a reference nobody seeded.

    A judge should be able to type their own booking reference and get a coherent
    world back, not a 404 that ends the demo. Generation is seeded from the
    reference, so the same reference always produces the same record — a company
    that invents different facts each time you call it is not a company.
    """
    s = _seed(company, kind, ref)
    base = {"reference": str(ref).upper(), "company": company,
            "generated": True, "created_at": now(conn)}
    if kind == "booking" and company == "skylink":
        delay = _pick(s, [0, 45, 95, 190, 260, 320])
        base.update({
            "kind": "flight", "flight_number": "SL" + str(100 + (s % 800)),
            "route": _pick(s, ["LHR-BER", "DEL-DXB", "MAN-CDG", "BOM-SIN"]),
            "distance_km": _pick(s, [930, 1850, 2200, 3900]),
            "scheduled_departure": ids.in_days(-14, frm=now(conn)),
            "status": "delayed" if delay >= 180 else ("on_time" if delay == 0 else "delayed"),
            "delay_minutes": delay,
            "disruption_reason": _pick(s, ["technical", "crew", "weather",
                                           "air_traffic_control", "technical"]),
            "fare_minor": 18_500 + (s % 40) * 500, "currency": "GBP",
            "passenger": "the cardholder"})
    elif kind == "booking" and company == "meridian":
        base.update({
            "kind": "stay", "property": _pick(s, ["Meridian Suites Kings Cross",
                                                  "Meridian Suites Bandra",
                                                  "Meridian Suites Marienplatz"]),
            "checkin": ids.in_days(-7, frm=now(conn)),
            "nights": 1 + (s % 4),
            "rate_minor": 12_000 + (s % 30) * 750, "currency": "GBP",
            "status": _pick(s, ["confirmed", "cancelled_by_property", "confirmed",
                                "checked_out"]),
            "cancelled_at": ids.in_days(-9, frm=now(conn)),
            "cancellation_reason": "overbooking"})
    elif kind == "order":
        total = 1_999 + (s % 60) * 100
        base.update({
            "kind": "order", "items": [{"sku": f"K-{s % 90000:05d}",
                                        "name": _pick(s, ["Bluetooth headphones",
                                                          "Cotton bedsheet set",
                                                          "Espresso machine",
                                                          "Running shoes, UK 9"]),
                                        "qty": 1, "price_minor": total}],
            "total_minor": total, "currency": _pick(s, ["INR", "GBP", "USD"]),
            "placed_at": ids.in_days(-11, frm=now(conn)),
            "status": _pick(s, ["delivered", "delivered", "in_transit", "delivered"]),
            "shipped_ref": f"TRK{s % 10_000_000:07d}"})
    elif kind == "subscription":
        base.update({
            "kind": "subscription", "plan": _pick(s, ["Standard monthly", "Premium annual",
                                                      "Family annual"]),
            "price_minor": _pick(s, [79_900, 149_900, 99_900]), "currency": "INR",
            "status": "active", "renews_at": ids.in_days(300, frm=now(conn)),
            "last_charged_at": ids.in_days(-(s % 20) - 1, frm=now(conn)),
            "cancel_route": "self_service"})
    elif kind == "invoice":
        base.update({
            "kind": "invoice", "period": "last month",
            "total_minor": 2_400 + (s % 50) * 60, "currency": "GBP",
            "lines": [{"label": "Monthly plan", "amount_minor": 1_800},
                      {"label": "Out-of-bundle data", "amount_minor": 600 + (s % 50) * 60}],
            "status": "issued"})
    else:
        base.update({"kind": kind, "status": "unknown"})
    return put(conn, company, kind, ref, base)


def get_or_generate(conn, company: str, kind: str, ref: str) -> dict:
    return fetch(conn, company, kind, ref) or generate(conn, company, kind, ref)


# ─────────────────────────────────────────────────────────────────────────────
# behaviour: how a company answers a request
# ─────────────────────────────────────────────────────────────────────────────
# Each company's refusal policy, expressed as data. Cited rights matter: a message
# that names a right the company recognises gets a different answer from one that
# does not, which is the whole reason Agent X bothers to establish entitlement
# before it writes.
REFUSAL_POLICY: dict[str, dict] = {
    "kartly": {
        "duplicate_charge": {"first": "under_review", "after_days": 5,
                             "on_chase": "under_review", "on_escalation": "approved",
                             "note": "Kartly routes duplicate-charge claims to a review "
                                     "queue and only releases them on escalation."},
        "wrong_item_received": {"first": "approved", "after_days": 0,
                                "on_chase": "approved", "on_escalation": "approved",
                                "note": "Wrong-item replacements are auto-approved."},
        "order_not_received": {"first": "under_review", "after_days": 3,
                               "on_chase": "approved", "on_escalation": "approved"},
        "*": {"first": "under_review", "after_days": 5, "on_chase": "under_review",
              "on_escalation": "approved"},
    },
    "streamly": {
        "subscription_renewal_unexpected": {
            "first": "refused", "after_days": 1, "on_chase": "refused",
            "on_escalation": "approved",
            "refusal_reason": "Renewals are final once the new term has started. "
                              "See clause 7.2 of the Streamly terms.",
            "relents_on": ["us_rosca_click_to_cancel", "uk_ccr_2013", "uk_cra_2015"],
            "note": "Streamly's retention script refuses on first ask and pays when a "
                    "specific statutory right is cited."},
        "*": {"first": "under_review", "after_days": 3, "on_chase": "approved",
              "on_escalation": "approved"},
    },
    "meridian": {
        "hotel_booking_cancelled": {
            "first": "partial", "after_days": 2, "on_chase": "partial",
            "on_escalation": "approved",
            "refusal_reason": "We have refunded the room rate in full. We are not "
                              "able to cover the cost of alternative accommodation.",
            "note": "Meridian refunds the rate immediately and resists the difference "
                    "until the booking platform is brought in."},
        "*": {"first": "under_review", "after_days": 5, "on_chase": "approved",
              "on_escalation": "approved"},
    },
    "skylink": {
        "*": {"first": "acknowledged", "after_days": 14, "on_chase": "under_review",
              "on_escalation": "approved",
              "note": "SkyLink acknowledges claims immediately with a reference and "
                      "takes the full 14 days."},
    },
    "nimbus": {
        "*": {"first": "under_review", "after_days": 7, "on_chase": "approved",
              "on_escalation": "approved"},
    },
}


def policy_for(company: str, problem_type: str | None) -> dict:
    table = REFUSAL_POLICY.get(company, {})
    return table.get(problem_type or "*", table.get("*", {
        "first": "under_review", "after_days": 5, "on_chase": "approved",
        "on_escalation": "approved"}))


def cites_recognised_right(company: str, text: str) -> list[str]:
    """Which of this company's recognised rights the message actually names.

    Matched against the policy corpus's own citations and titles, so a letter that
    says "Consumer Contracts Regulations 2013" counts and one that says "I know my
    rights" does not. This is what makes Agent X's insistence on establishing
    entitlement before writing pay off inside the sandbox as well as outside it.
    """
    from agentx import policy as _pol
    corpus = _pol.corpus()
    hits = []
    low = (text or "").lower()
    for pid in COMPANIES.get(company, {}).get("recognises", []):
        pol = corpus.get(pid)
        if not pol:
            continue
        needles = [pol.title.lower()] + re.split(r"[;,]", pol.citation.lower())
        if any(n.strip()[:28] in low for n in needles if len(n.strip()) > 8):
            hits.append(pid)
    return hits
