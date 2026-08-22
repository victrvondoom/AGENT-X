"""
Sandbox providers — the adapter between Agent X's action verbs and the sandbox world.

The split is deliberate. `agentx/sandbox/world.py` is the OUTSIDE: five companies
with records, refusal policies and response times, which know nothing about Agent X.
This module is the adapter that speaks to them through the same `Provider`
interface a real integration would implement. Swapping a sandbox merchant for a
live one is a registry change, not an engine change — and if that were not true,
the abstraction would be decoration.

Everything here returns `mode="sandbox"`, and that label survives into the
execution record, the case chain and the resolution receipt. The one thing this
file must never do is make a sandbox action indistinguishable from a real one.

THE TICKET IS THE POINT

A refund request does not return a boolean. It opens a TICKET in the company's own
records — a persistent object with a reference, a status, a decision clock and a
chase count. Every later interaction reads and mutates that ticket, and
verification re-reads it plus the company's payment ledger. That is what makes
`Action → Evidence → Verification` a real loop rather than a diagram: the thing
Agent X verifies is state it does not own.
"""
from __future__ import annotations

from agentx import ids, normalize
from agentx.execution.providers.base import Provider, ProviderResult
from agentx.sandbox import world


def _ticket_ref(company: str, case_id: str) -> str:
    return f"{company[:3].upper()}-{abs(hash((company, case_id))) % 900000 + 100000}"


def _open_ticket(conn, company: str, case_id: str, problem_type: str | None,
                 amount_minor: int | None, currency: str | None,
                 channel: str, body: str) -> dict:
    pol = world.policy_for(company, problem_type)
    cited = world.cites_recognised_right(company, body or "")
    outcome = pol.get("first", "under_review")

    # A message that names a right this company recognises can skip its first
    # refusal. That is not generosity — it is why establishing entitlement before
    # writing is worth the effort, and the sandbox has to reward it or the
    # architecture is untested.
    relents = set(pol.get("relents_on") or [])
    if outcome == "refused" and relents and (set(cited) & relents):
        outcome = "approved"

    t = {
        "ticket_ref": _ticket_ref(company, case_id),
        "company": company, "case_id": case_id, "problem_type": problem_type,
        "channel": channel,
        "amount_claimed_minor": amount_minor, "currency": currency,
        "status": outcome, "opened_at": world.now(conn),
        "decision_due": ids.in_days(pol.get("after_days", 5), frm=world.now(conn)),
        "chases": 0, "escalated": False,
        "cited_rights": cited,
        "note": pol.get("note", ""),
        "refusal_reason": pol.get("refusal_reason") if outcome in ("refused", "partial") else None,
        "history": [{"at": world.now(conn), "event": "opened", "status": outcome}],
    }
    if outcome == "approved":
        _post_refund(conn, company, t, amount_minor)
    if outcome == "partial":
        part = int((amount_minor or 0) * 0.6)
        t["amount_approved_minor"] = part
        _post_refund(conn, company, t, part)
    world.put(conn, company, "ticket", t["ticket_ref"], t)
    return t


def _post_refund(conn, company: str, ticket: dict, amount_minor: int | None) -> None:
    """Write a credit into the company's payment ledger.

    Separate from the ticket on purpose. A ticket saying "approved" is the
    company's INTENTION; a ledger entry is the money. Verification checks the
    ledger, because "they said they refunded me" is the single most common
    unresolved consumer complaint there is.
    """
    if not amount_minor:
        return
    ref = f"RFND-{ticket['ticket_ref']}"
    # A ledger ACCUMULATES. Deriving the ref from the ticket alone meant a second
    # posting against the same ticket — the escalation top-up after a partial
    # refund is exactly that — overwrote the first row instead of adding to it,
    # so a consumer paid 60% then the remaining 40% ended up with a ledger
    # showing 40% and a verification that reported the balance still outstanding.
    existing = world.fetch(conn, company, "payment", ref) or {}
    total = int(existing.get("amount_minor") or 0) + int(amount_minor)
    postings = list(existing.get("postings") or [])
    postings.append({"amount_minor": int(amount_minor), "at": world.now(conn)})
    world.put(conn, company, "payment", ref, {
        "reference": ref, "kind": "refund", "ticket_ref": ticket["ticket_ref"],
        "amount_minor": total, "currency": ticket.get("currency"),
        "postings": postings,
        "posted_at": existing.get("posted_at") or world.now(conn),
        "last_posted_at": world.now(conn), "status": "posted"})
    ticket["refund_reference"] = ref
    ticket["amount_approved_minor"] = total


def _receipt_text(company: str, t: dict) -> str:
    c = world.COMPANIES[company]
    lines = [
        f"{c['name']} — case acknowledgement",
        f"Reference: {t['ticket_ref']}",
        f"Received: {t['opened_at']}",
        f"Status: {t['status'].replace('_', ' ')}",
    ]
    if t.get("amount_claimed_minor"):
        lines.append("Amount claimed: " + normalize.fmt_money(
            t["amount_claimed_minor"], t.get("currency")))
    if t.get("amount_approved_minor"):
        lines.append("Amount approved: " + normalize.fmt_money(
            t["amount_approved_minor"], t.get("currency")))
    if t.get("refusal_reason"):
        lines.append("Reason: " + t["refusal_reason"])
    if t.get("refund_reference"):
        lines.append("Refund reference: " + t["refund_reference"])
    lines.append(f"We aim to respond by {t['decision_due']}.")
    return "\n".join(lines)


def _outcome_result(provider: Provider, company: str, t: dict,
                    conn) -> ProviderResult:
    mapping = {"approved": "accepted", "partial": "accepted", "refused": "refused",
               "under_review": "pending", "acknowledged": "pending"}
    days = ids.days_between(world.now(conn), t.get("decision_due"))
    return ProviderResult(
        ok=True, outcome=mapping.get(t["status"], "pending"),
        provider=provider.id, mode=provider.mode,
        external_ref=t["ticket_ref"],
        message=(t.get("refusal_reason")
                 or f"{world.COMPANIES[company]['name']} recorded the request as "
                    f"{t['status'].replace('_', ' ')}."),
        data={"status": t["status"], "chases": t["chases"],
              "escalated": t["escalated"],
              "amount_approved_minor": t.get("amount_approved_minor"),
              "currency": t.get("currency"),
              "refund_reference": t.get("refund_reference"),
              "cited_rights": t.get("cited_rights", [])},
        evidence_text=_receipt_text(company, t),
        evidence_kind="confirmation_page",
        responds_in_days=max(0.0, days) if days is not None else None)


class _SandboxCompanyProvider(Provider):
    """Shared behaviour for the five company providers."""

    mode = "sandbox"
    company = ""

    @property
    def label(self) -> str:                     # type: ignore[override]
        return f"{world.COMPANIES[self.company]['name']} (sandbox)"

    # ── read ──────────────────────────────────────────────────────────────
    def do_retrieve(self, p: dict) -> ProviderResult:
        kind = p.get("record_kind") or "order"
        ref = p.get("reference")
        if not ref:
            return ProviderResult(False, "not_found", self.id, self.mode,
                                  message="No reference was supplied, so there is "
                                          "nothing to look up.")
        rec = world.get_or_generate(self.conn, self.company, kind, ref)
        return ProviderResult(
            True, "done", self.id, self.mode, external_ref=str(ref),
            message=f"{world.COMPANIES[self.company]['name']} returned the {kind} record.",
            data=rec,
            evidence_text=_record_text(self.company, kind, rec),
            evidence_kind="provider_record")

    def do_inspect(self, p: dict) -> ProviderResult:
        return self.do_retrieve(p)

    def do_search(self, p: dict) -> ProviderResult:
        rows = world.all_objects(self.conn, self.company)
        return ProviderResult(True, "done", self.id, self.mode,
                              message=f"{len(rows)} record(s) on file.",
                              data={"records": [{"kind": r["kind"],
                                                 "reference": r["reference"]}
                                                for r in rows][:50]})

    # ── act ───────────────────────────────────────────────────────────────
    def do_request_refund(self, p: dict) -> ProviderResult:
        t = _open_ticket(self.conn, self.company, p.get("case_id", "unknown"),
                         p.get("problem_type"), p.get("amount_minor"),
                         p.get("currency"), p.get("channel", "portal"),
                         p.get("body", ""))
        return _outcome_result(self, self.company, t, self.conn)

    def do_submit_form(self, p: dict) -> ProviderResult:
        t = _open_ticket(self.conn, self.company, p.get("case_id", "unknown"),
                         p.get("problem_type"), p.get("amount_minor"),
                         p.get("currency"), p.get("channel", "web_form"),
                         p.get("body", ""))
        return _outcome_result(self, self.company, t, self.conn)

    def do_follow_up(self, p: dict) -> ProviderResult:
        ref = p.get("external_ref")
        t = world.fetch(self.conn, self.company, "ticket", ref) if ref else None
        if not t:
            return ProviderResult(False, "not_found", self.id, self.mode,
                                  message=f"No open case with reference {ref!r}.")
        pol = world.policy_for(self.company, t.get("problem_type"))
        t["chases"] += 1
        due_in = ids.days_between(world.now(self.conn), t.get("decision_due"))
        if due_in is not None and due_in > 0 and t["status"] in ("under_review", "acknowledged"):
            t["history"].append({"at": world.now(self.conn), "event": "chased",
                                 "status": t["status"],
                                 "note": "still within the stated response time"})
            world.put(self.conn, self.company, "ticket", t["ticket_ref"], t)
            r = _outcome_result(self, self.company, t, self.conn)
            r.message = (f"{world.COMPANIES[self.company]['name']} says the case is "
                         f"still within its stated response time "
                         f"(due {t['decision_due']}).")
            return r

        new = pol.get("on_chase", "approved")
        if new == "approved" and t["status"] != "approved":
            t["status"] = "approved"
            _post_refund(self.conn, self.company, t, t.get("amount_claimed_minor"))
        elif new == "refused":
            t["status"] = "refused"
            t["refusal_reason"] = pol.get("refusal_reason") or t.get("refusal_reason")
        t["history"].append({"at": world.now(self.conn), "event": "chased",
                             "status": t["status"]})
        world.put(self.conn, self.company, "ticket", t["ticket_ref"], t)
        return _outcome_result(self, self.company, t, self.conn)

    def do_escalate(self, p: dict) -> ProviderResult:
        ref = p.get("external_ref")
        t = world.fetch(self.conn, self.company, "ticket", ref) if ref else None
        if not t:
            return ProviderResult(False, "not_found", self.id, self.mode,
                                  message=f"No open case with reference {ref!r} to escalate.")
        pol = world.policy_for(self.company, t.get("problem_type"))
        t["escalated"] = True
        new = pol.get("on_escalation", "approved")
        if new == "approved":
            t["status"] = "approved"
            outstanding = (t.get("amount_claimed_minor") or 0) - (t.get("amount_approved_minor") or 0)
            if outstanding > 0:
                # _post_refund accumulates and sets amount_approved_minor to the
                # running ledger total itself; assigning the claimed amount here
                # would overwrite a real figure with an assumed one.
                _post_refund(self.conn, self.company, t, outstanding)
            t["refusal_reason"] = None
        t["history"].append({"at": world.now(self.conn), "event": "escalated",
                             "status": t["status"], "to": p.get("to", "supervisor")})
        world.put(self.conn, self.company, "ticket", t["ticket_ref"], t)
        r = _outcome_result(self, self.company, t, self.conn)
        r.message = (f"Escalated to {p.get('to', 'a supervisor')}. "
                     f"{world.COMPANIES[self.company]['name']} now records the case as "
                     f"{t['status'].replace('_', ' ')}.")
        return r

    # ── verify ────────────────────────────────────────────────────────────
    def do_verify(self, p: dict) -> ProviderResult:
        """Re-read the company's own records. Never trusts the earlier response.

        Checks the LEDGER, not the ticket status, when money is involved: a
        company that says "approved" and never posts the credit is the most common
        unresolved consumer complaint there is, and a verification that reads the
        promise instead of the payment would miss it entirely.
        """
        ref = p.get("external_ref")
        t = world.fetch(self.conn, self.company, "ticket", ref) if ref else None
        if not t:
            return ProviderResult(False, "not_found", self.id, self.mode,
                                  message=f"Nothing on file under {ref!r} to verify.")
        payment = None
        if t.get("refund_reference"):
            payment = world.fetch(self.conn, self.company, "payment",
                                  t["refund_reference"])
        verified = bool(payment and payment.get("status") == "posted")
        claimed = t.get("amount_claimed_minor") or 0
        posted = (payment or {}).get("amount_minor") or 0
        full = verified and posted >= claimed
        lines = [f"{world.COMPANIES[self.company]['name']} — record check",
                 f"Reference: {t['ticket_ref']}",
                 f"Status: {t['status']}",
                 f"Checked at: {world.now(self.conn)}"]
        if payment:
            lines += [f"Credit reference: {payment['reference']}",
                      "Credit posted: " + normalize.fmt_money(posted, payment.get("currency")),
                      f"Posted at: {payment.get('posted_at')}"]
        else:
            lines.append("No credit found in the payment ledger.")
        return ProviderResult(
            # FULL, not merely "something was posted". A partial credit is not a
            # resolved case: reporting `done` here let the follow-up agent mark a
            # case RESOLVED while its own receipt still read "the balance is
            # still outstanding" — precisely the claim-without-evidence this
            # product exists to refuse. Partial stays `pending`, so the case
            # keeps chasing and escalates for the remainder.
            ok=True, outcome="done" if full else "pending",
            provider=self.id, mode=self.mode, external_ref=t["ticket_ref"],
            message=("Refund confirmed in the company's payment ledger."
                     if full else
                     ("Partial credit posted; the balance is still outstanding."
                      if verified else
                      "The company has not posted a credit yet.")),
            data={"verified": verified, "full": full, "posted_minor": posted,
                  "claimed_minor": claimed, "status": t["status"],
                  "currency": t.get("currency")},
            evidence_text="\n".join(lines), evidence_kind="provider_record")


def _record_text(company: str, kind: str, rec: dict) -> str:
    name = world.COMPANIES[company]["name"]
    out = [f"{name} — {kind} record", f"Reference: {rec.get('reference')}"]
    for k, v in rec.items():
        if k in ("reference", "company", "generated", "history"):
            continue
        if isinstance(v, (dict, list)):
            v = str(v)
        out.append(f"{k.replace('_', ' ').title()}: {v}")
    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────────────────
# the five companies
# ─────────────────────────────────────────────────────────────────────────────
class SkylinkProvider(_SandboxCompanyProvider):
    id = "sandbox:skylink"
    family = "booking"
    company = "skylink"
    serves = ("skylink", "SkyLink Airways")

    def do_cancel(self, p: dict) -> ProviderResult:
        rec = world.get_or_generate(self.conn, "skylink", "booking", p.get("reference", ""))
        rec["status"] = "cancelled_by_passenger"
        rec["cancelled_at"] = world.now(self.conn)
        world.put(self.conn, "skylink", "booking", rec["reference"], rec)
        return ProviderResult(True, "accepted", self.id, self.mode,
                              external_ref=rec["reference"],
                              message="Booking cancelled with SkyLink Airways.",
                              data=rec,
                              evidence_text=_record_text("skylink", "booking", rec),
                              evidence_kind="cancellation_notice")


class MeridianProvider(_SandboxCompanyProvider):
    id = "sandbox:meridian"
    family = "booking"
    company = "meridian"
    serves = ("meridian", "Meridian Suites")


class KartlyProvider(_SandboxCompanyProvider):
    id = "sandbox:kartly"
    family = "merchant"
    company = "kartly"
    serves = ("kartly", "Kartly")


class StreamlyProvider(_SandboxCompanyProvider):
    id = "sandbox:streamly"
    family = "subscription"
    company = "streamly"
    serves = ("streamly", "Streamly")

    def do_cancel(self, p: dict) -> ProviderResult:
        ref = p.get("reference") or p.get("account") or "SUB-DEFAULT"
        sub = world.get_or_generate(self.conn, "streamly", "subscription", ref)
        if sub.get("status") == "cancelled":
            return ProviderResult(
                True, "done", self.id, self.mode, external_ref=str(ref),
                message="This plan was already cancelled.", data=sub,
                evidence_text=_record_text("streamly", "subscription", sub),
                evidence_kind="confirmation_page")
        sub["status"] = "cancelled"
        sub["cancelled_at"] = world.now(self.conn)
        sub["access_until"] = sub.get("renews_at")
        sub["cancellation_reference"] = f"CAN-{str(ref).upper()[-6:]}"
        world.put(self.conn, "streamly", "subscription", ref, sub)
        page = ("Streamly — your plan is cancelled\n"
                f"Plan: {sub.get('plan')}\n"
                f"Cancellation reference: {sub['cancellation_reference']}\n"
                f"Cancelled at: {sub['cancelled_at']}\n"
                f"You keep access until: {sub.get('access_until')}\n"
                "You will not be charged again.")
        return ProviderResult(True, "accepted", self.id, self.mode,
                              external_ref=sub["cancellation_reference"],
                              message="Streamly confirmed the cancellation.",
                              data=sub, evidence_text=page,
                              evidence_kind="confirmation_page")


class NimbusProvider(_SandboxCompanyProvider):
    id = "sandbox:nimbus"
    family = "telecom"
    company = "nimbus"
    serves = ("nimbus", "Nimbus Mobile")


# ─────────────────────────────────────────────────────────────────────────────
# cross-cutting sandbox providers
# ─────────────────────────────────────────────────────────────────────────────
class SandboxPaymentProvider(Provider):
    """The user's card issuer. Reads the statement; files a dispute.

    Separate from the merchant providers because it is a separate counterparty
    with a separate relationship — and because a chargeback is the one action in
    the catalogue that can damage the user's standing with a merchant, which is
    why the governor holds it at level 4 with a mandatory explicit approval.
    """

    id = "sandbox:issuer"
    family = "payment"
    mode = "sandbox"
    label = "Northgate Bank (sandbox card issuer)"

    def do_retrieve(self, p: dict) -> ProviderResult:
        ref = p.get("reference") or p.get("card") or "STATEMENT"
        rec = world.get_or_generate(self.conn, "kartly", "order", ref)
        return ProviderResult(True, "done", self.id, self.mode, external_ref=str(ref),
                              message="Statement lines returned.", data=rec,
                              evidence_text=_record_text("kartly", "order", rec),
                              evidence_kind="bank_statement")

    def do_escalate(self, p: dict) -> ProviderResult:
        ref = f"DSP-{abs(hash(p.get('case_id', ''))) % 900000 + 100000}"
        rec = {"reference": ref, "kind": "dispute",
               "case_id": p.get("case_id"), "amount_minor": p.get("amount_minor"),
               "currency": p.get("currency"), "status": "filed",
               "filed_at": world.now(self.conn),
               "provisional_credit": True,
               "decision_due": ids.in_days(45, frm=world.now(self.conn)),
               "reason_code": p.get("reason_code", "duplicate_processing")}
        world.put(self.conn, "issuer", "claim", ref, rec)
        page = ("Northgate Bank — dispute filed\n"
                f"Dispute reference: {ref}\n"
                f"Reason code: {rec['reason_code']}\n"
                "Amount: " + normalize.fmt_money(rec["amount_minor"], rec["currency"]) + "\n"
                "A provisional credit has been applied while we investigate.\n"
                f"Decision expected by {rec['decision_due']}.")
        return ProviderResult(True, "accepted", self.id, self.mode, external_ref=ref,
                              message="Dispute filed with the card issuer; provisional "
                                      "credit applied.",
                              data=rec, responds_in_days=45,
                              evidence_text=page, evidence_kind="confirmation_page")

    def do_verify(self, p: dict) -> ProviderResult:
        ref = p.get("external_ref")
        rec = world.fetch(self.conn, "issuer", "claim", ref) if ref else None
        if not rec:
            return ProviderResult(False, "not_found", self.id, self.mode,
                                  message=f"No dispute on file under {ref!r}.")
        return ProviderResult(True, "done", self.id, self.mode, external_ref=ref,
                              message=f"Dispute {ref} is {rec['status']}.",
                              data=rec,
                              evidence_text=_record_text("issuer", "claim", rec),
                              evidence_kind="provider_record")


class SandboxEmailProvider(Provider):
    """A mailbox that delivers, records, and produces a reply on the company's clock.

    Modelled rather than mocked: a message goes to the company's real support
    address in the sandbox world, opens or advances that company's ticket, and
    returns the auto-acknowledgement text as capturable evidence. `mode` stays
    `sandbox`, so no user is ever told an email left the building when it did not.
    """

    id = "sandbox:mail"
    family = "email"
    mode = "sandbox"
    label = "Agent X sandbox mailbox"

    def do_email(self, p: dict) -> ProviderResult:
        company = world.resolve_company(p.get("counterparty") or p.get("merchant"))
        to = (world.COMPANIES.get(company, {}).get("support_email")
              if company else p.get("to"))
        if not to:
            return ProviderResult(
                False, "error", self.id, self.mode,
                message="No support address is known for this counterparty, and the "
                        "sandbox will not invent one.")
        if company:
            t = _open_ticket(self.conn, company, p.get("case_id", "unknown"),
                             p.get("problem_type"), p.get("amount_minor"),
                             p.get("currency"), "email", p.get("body", ""))
            body = _receipt_text(company, t)
            return ProviderResult(
                True, "accepted", self.id, self.mode, external_ref=t["ticket_ref"],
                message=f"Delivered to {to}; auto-acknowledged as {t['ticket_ref']}.",
                data={"to": to, "subject": p.get("subject"), "status": t["status"],
                      "amount_approved_minor": t.get("amount_approved_minor"),
                      "currency": t.get("currency"),
                      "refund_reference": t.get("refund_reference")},
                evidence_text=body, evidence_kind="email",
                responds_in_days=world.policy_for(company, p.get("problem_type"))
                .get("after_days", 5))
        return ProviderResult(True, "pending", self.id, self.mode,
                              message=f"Delivered to {to}. No auto-reply.",
                              data={"to": to, "subject": p.get("subject")},
                              responds_in_days=5)

    def do_follow_up(self, p: dict) -> ProviderResult:
        company = world.resolve_company(p.get("counterparty") or p.get("merchant"))
        if not company:
            return ProviderResult(False, "error", self.id, self.mode,
                                  message="No known counterparty to chase.")
        prov = PROVIDER_BY_COMPANY[company]().bind(self.conn)
        return prov.do_follow_up(p)


class SandboxBrowserProvider(Provider):
    """A browser agent over the sandbox companies' web pages.

    Deliberately shaped like a real browser agent — navigate, read the page, fill
    a form, detect a confirmation, capture the page as evidence — so that swapping
    in Playwright or a hosted browser API is a provider registration and nothing
    else. The four verbs below are the interface that promise has to hold at.
    """

    id = "sandbox:browser"
    family = "browser"
    mode = "sandbox"
    label = "Agent X sandbox browser"

    def do_navigate(self, p: dict) -> ProviderResult:
        url = p.get("url") or ""
        company = world.resolve_company(p.get("counterparty")) or _company_from_url(url)
        if not company:
            return ProviderResult(
                False, "not_found", self.id, self.mode,
                message=f"The sandbox browser only reaches the sandbox companies; "
                        f"{url or 'that address'} is outside it.")
        page = _render_page(self.conn, company, p)
        return ProviderResult(True, "done", self.id, self.mode,
                              external_ref=world.COMPANIES[company]["portal"],
                              message=f"Loaded {world.COMPANIES[company]['portal']}.",
                              data={"company": company, "title": page.splitlines()[0]},
                              evidence_text=page, evidence_kind="screenshot")

    def do_inspect(self, p: dict) -> ProviderResult:
        return self.do_navigate(p)

    def do_submit_form(self, p: dict) -> ProviderResult:
        company = world.resolve_company(p.get("counterparty")) or _company_from_url(p.get("url", ""))
        if not company:
            return ProviderResult(False, "not_found", self.id, self.mode,
                                  message="No sandbox page to submit that form to.")
        prov = PROVIDER_BY_COMPANY[company]().bind(self.conn)
        res = prov.do_submit_form(p)
        # A form submission is only complete when the site says so. The
        # confirmation text is the evidence, and a submission with no
        # confirmation is reported as pending rather than accepted.
        if res.evidence_text and "Reference:" in res.evidence_text:
            res.evidence_kind = "confirmation_page"
        else:
            res.outcome = "pending"
            res.message += " No confirmation was rendered, so this is unverified."
        return res

    def do_retrieve(self, p: dict) -> ProviderResult:
        """Fetch a company's published terms — what `merchant_terms_lookup` needs."""
        company = world.resolve_company(p.get("counterparty"))
        if not company:
            return ProviderResult(
                False, "not_found", self.id, self.mode,
                message="No published terms are reachable for this counterparty, so "
                        "Agent X treats the merchant's own policy as unknown.")
        c = world.COMPANIES[company]
        text = (f"{c['name']} — customer terms (extract)\n"
                f"Retrieved: {world.now(self.conn)}\n"
                f"Source: {c['portal']}/terms\n\n"
                f"Response time: we aim to respond within {c['sla_days']} working days.\n"
                f"Rights we apply: {', '.join(c['recognises'])}.\n"
                f"About us: {c['blurb']}")
        return ProviderResult(True, "done", self.id, self.mode,
                              external_ref=f"{c['portal']}/terms",
                              message=f"Read {c['name']}'s published terms.",
                              data={"sla_days": c["sla_days"],
                                    "recognises": c["recognises"]},
                              evidence_text=text, evidence_kind="terms")


def _company_from_url(url: str) -> str | None:
    for cid, c in world.COMPANIES.items():
        if cid in (url or "").lower():
            return cid
    return None


def _render_page(conn, company: str, p: dict) -> str:
    c = world.COMPANIES[company]
    ref = p.get("reference")
    lines = [f"{c['name']} — {p.get('page', 'help centre')}",
             f"URL: {c['portal']}",
             f"Loaded at: {world.now(conn)}", ""]
    if ref:
        rec = world.fetch(conn, company, p.get("record_kind", "order"), ref)
        if rec:
            lines.append("On this page:")
            for k, v in rec.items():
                if k not in ("history", "company", "generated"):
                    lines.append(f"  {k.replace('_', ' ')}: {v}")
        else:
            lines.append(f"We could not find anything under {ref}.")
    lines += ["", "Options on this page:",
              "  [ Request a refund ]", "  [ Contact us ]", "  [ Cancel ]"]
    return "\n".join(lines)


PROVIDER_BY_COMPANY = {
    "skylink": SkylinkProvider, "meridian": MeridianProvider,
    "kartly": KartlyProvider, "streamly": StreamlyProvider,
    "nimbus": NimbusProvider,
}

ALL = (SkylinkProvider, MeridianProvider, KartlyProvider, StreamlyProvider,
       NimbusProvider, SandboxPaymentProvider, SandboxEmailProvider,
       SandboxBrowserProvider)
