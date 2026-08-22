"""
Live providers — real integrations behind the exact interface the sandbox uses.

This file exists to make good on the product's central architectural claim:
swapping a sandbox provider for a real one is a registration change, never a
rewrite of the planner, the governor, or the runner. `LiveEmailProvider` below
is that proof — it implements `do_email` the same way `SandboxEmailProvider`
does, returns the same `ProviderResult` shape, and the only thing that changes
anywhere else in the system is `mode = "live"` propagating into the execution
record, the case chain, and the receipt.

NONE OF THESE SHIP ENABLED

Each live provider is registered only when ITS OWN configuration is present
(`configured()` on the class), checked at `bootstrap()` time. A deployment that
sets up SMTP gets `live:smtp` registered automatically; one that doesn't gets
nothing — never a silent fallback that pretends a real send happened. This is
the same discipline `AGENT_X_ROOT_KEY`/`AGENT_X_SIGNING_KEY` already apply to the
trust spine's key material: present and used, or absent and honestly reported.

WHY EMAIL FIRST

It is the one channel every counterparty in the ontology already has (a support
address), needs no third-party API key or approval process to stand up (an SMTP
relay is commodity infrastructure), and — critically for what this repository can
respons­ibly ship — needs no scraping, no credential storage for a third-party
site, and no risk of automating an interaction a real company did not consent to
being automated. A live browser or merchant-API provider is a natural next
addition behind the same `Provider` interface; it is not implemented here because
each one is a bespoke integration with its own terms of service to respect, and
committing to one without a specific target would be exactly the kind of
unauthorised, semi-real integration this product's own rules refuse to ship.

VERIFICATION IS HONESTLY ABSENT

`do_verify` is deliberately not implemented. Confirming a live counterparty's
reply needs an IMAP mailbox, a reply-matching strategy, and its own operational
surface — building a half-working version of that would be worse than admitting
it is not built. `agentx/execution/runner.py:verify()` already handles a provider
with no `do_verify` correctly: it reports `verified = "unverifiable"`, which is
the honest answer, not a gap silently papered over.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid

from .base import Provider, ProviderResult

_REQUIRED_ENV = ("AGENT_X_SMTP_HOST", "AGENT_X_SMTP_USER",
                 "AGENT_X_SMTP_PASSWORD", "AGENT_X_SMTP_FROM")


class LiveEmailProvider(Provider):
    """Sends real email over SMTP/STARTTLS (or implicit TLS on 465).

    Configuration, all via environment, all required:

        AGENT_X_SMTP_HOST       mail server hostname
        AGENT_X_SMTP_PORT       default 587 (STARTTLS); use 465 for implicit TLS
        AGENT_X_SMTP_USER       account used to authenticate
        AGENT_X_SMTP_PASSWORD   its password or app-specific token
        AGENT_X_SMTP_FROM       the From: address on outgoing mail
        AGENT_X_SMTP_TLS        "0" to force implicit TLS (port 465) instead of
                                 STARTTLS; any other value (or unset) uses STARTTLS

    A missing recipient is refused before anything touches the network — a live
    provider does not invent an address the way the sandbox's mailbox can invent
    a company's support line, because guessing wrong here sends a real email to
    the wrong place.
    """

    id = "live:smtp"
    family = "email"
    mode = "live"
    label = "SMTP (live)"
    serves = ("*",)

    @staticmethod
    def configured() -> bool:
        return all(os.environ.get(k, "").strip() for k in _REQUIRED_ENV)

    def do_email(self, p: dict) -> ProviderResult:
        to = (p.get("to") or p.get("recipient") or "").strip()
        if not to:
            return ProviderResult(
                False, "error", self.id, self.mode,
                message="No recipient address was supplied. A live provider will "
                        "not guess one — that is how a real email reaches the "
                        "wrong company.")

        host = os.environ.get("AGENT_X_SMTP_HOST", "")
        port = int(os.environ.get("AGENT_X_SMTP_PORT", "587"))
        user = os.environ.get("AGENT_X_SMTP_USER", "")
        password = os.environ.get("AGENT_X_SMTP_PASSWORD", "")
        sender = os.environ.get("AGENT_X_SMTP_FROM", "")
        implicit_tls = os.environ.get("AGENT_X_SMTP_TLS", "1") in ("0", "false", "no")

        if not self.configured():
            return ProviderResult(
                False, "error", self.id, self.mode,
                message="AGENT_X_SMTP_* is not fully configured; refusing to "
                        "attempt a send with partial credentials.")

        msg = EmailMessage()
        msg["Message-ID"] = make_msgid()
        msg["Subject"] = p.get("subject") or "Consumer resolution case"
        msg["From"] = sender
        msg["To"] = to
        msg.set_content(p.get("body") or "")

        try:
            if implicit_tls:
                with smtplib.SMTP_SSL(host, port, timeout=20,
                                      context=ssl.create_default_context()) as s:
                    s.login(user, password)
                    s.send_message(msg)
            else:
                with smtplib.SMTP(host, port, timeout=20) as s:
                    s.starttls(context=ssl.create_default_context())
                    s.login(user, password)
                    s.send_message(msg)
        except (smtplib.SMTPException, OSError, ssl.SSLError) as e:
            return ProviderResult(
                False, "error", self.id, self.mode,
                message=f"SMTP delivery failed: {type(e).__name__}: {e}",
                retryable=True)

        evidence = (
            f"Sent via SMTP ({host})\n"
            f"Message-ID: {msg['Message-ID']}\n"
            f"From: {sender}\n"
            f"To: {to}\n"
            f"Subject: {msg['Subject']}\n\n"
            f"{p.get('body') or ''}"
        )
        return ProviderResult(
            True, "accepted", self.id, self.mode,
            external_ref=msg["Message-ID"],
            message=f"Delivered to {to} via SMTP.",
            data={"to": to, "subject": msg["Subject"]},
            evidence_text=evidence, evidence_kind="email",
            # A live counterparty's response time is not something we are told at
            # send time the way the sandbox's stated SLA is. Reporting one here
            # would be exactly the kind of invented number this product's evidence
            # layer refuses to produce for anything else.
            responds_in_days=None)


# Providers whose `configured()` is checked at bootstrap; add new live providers
# here rather than hand-wiring them into __init__.py's bootstrap().
ALL = (LiveEmailProvider,)
