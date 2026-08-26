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
import urllib.request
import urllib.error
import urllib.parse
import json

from .base import Provider, ProviderResult, ErrorCode

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
        except smtplib.SMTPAuthenticationError as e:
            # The credentials themselves are wrong or expired — retrying the same
            # send immediately fails the same way. This needs a person to fix the
            # configured password/token, not another attempt.
            return ProviderResult(
                False, "error", self.id, self.mode,
                message="Agent X could not sign in to send this email — the "
                        "configured mail account needs to be reconnected.",
                technical_detail=f"{type(e).__name__}: {e}",
                error_code=ErrorCode.AUTH_REQUIRED, retryable=False)
        except TimeoutError as e:
            return ProviderResult(
                False, "error", self.id, self.mode,
                message="The mail server did not respond in time.",
                technical_detail=f"{type(e).__name__}: {e}",
                error_code=ErrorCode.TIMEOUT, retryable=True)
        except (smtplib.SMTPException, OSError, ssl.SSLError) as e:
            return ProviderResult(
                False, "error", self.id, self.mode,
                message=f"SMTP delivery failed: {type(e).__name__}: {e}",
                technical_detail=f"{type(e).__name__}: {e}",
                error_code=ErrorCode.RETRYABLE, retryable=True)

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


class LiveBrowserProvider(Provider):
    """Fetches real data from URLs via HTTP GET."""
    id = "live:browser"
    family = "browser"
    mode = "live"
    label = "HTTP Browser (live)"
    serves = ("*",)

    @staticmethod
    def configured() -> bool:
        return os.environ.get("AGENT_X_SANDBOX", "1") in ("0", "false", "no")

    def do_navigate(self, p: dict) -> ProviderResult:
        url = p.get("url") or ""
        if not url.startswith("http"):
            return ProviderResult(False, "error", self.id, self.mode,
                                  message="Valid URL required",
                                  error_code=ErrorCode.INVALID_INPUT)
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 AgentX'})
            with urllib.request.urlopen(req, timeout=15) as response:
                content = response.read().decode('utf-8', errors='ignore')
                status = response.getcode()
            return ProviderResult(True, "accepted", self.id, self.mode,
                                  data={"status": status, "length": len(content)},
                                  evidence_text=content[:4000], evidence_kind="html")
        except urllib.error.HTTPError as e:
            if e.code == 401 or e.code == 403:
                code, retryable = ErrorCode.AUTH_REQUIRED, False
            elif e.code == 429:
                code, retryable = ErrorCode.RATE_LIMITED, True
            elif e.code >= 500:
                code, retryable = ErrorCode.RETRYABLE, True
            else:
                code, retryable = ErrorCode.EXTERNAL_REJECTED, False
            retry_after = None
            hdr = e.headers.get("Retry-After") if e.headers else None
            if hdr and hdr.strip().isdigit():
                retry_after = float(hdr.strip())
            return ProviderResult(
                False, "error", self.id, self.mode,
                message=f"The site returned {e.code} {e.reason}.",
                technical_detail=str(e), error_code=code, retryable=retryable,
                retry_after=retry_after)
        except TimeoutError as e:
            return ProviderResult(
                False, "error", self.id, self.mode,
                message="The site did not respond in time.",
                technical_detail=f"{type(e).__name__}: {e}",
                error_code=ErrorCode.TIMEOUT, retryable=True)
        except Exception as e:
            return ProviderResult(
                False, "error", self.id, self.mode,
                message="Could not reach that page.",
                technical_detail=f"{type(e).__name__}: {e}",
                error_code=ErrorCode.RETRYABLE, retryable=True)

    def do_retrieve(self, p: dict) -> ProviderResult:
        return self.do_navigate(p)


class LiveMerchantProvider(Provider):
    """Interacts with merchant APIs.

    Not yet implemented: there is no real HTTP call behind this class, so
    `configured()` reports False until one exists — the same discipline
    `LiveEmailProvider` applies to its own credentials. Registering this as
    `mode='live'` without an actual integration would let a real refund case
    receive a fabricated success.
    """
    id = "live:merchant"
    family = "merchant"
    mode = "live"
    label = "HTTP Merchant API (live)"
    serves = ("*",)

    @staticmethod
    def configured() -> bool:
        return False

    def do_request_refund(self, p: dict) -> ProviderResult:
        return ProviderResult(
            False, "error", self.id, self.mode,
            message="Live merchant refund requests are not implemented yet.",
            error_code=ErrorCode.TOOL_UNAVAILABLE, retryable=False)


class LiveBookingProvider(Provider):
    """Inspects bookings via real endpoints.

    Not yet implemented: there is no real HTTP call behind this class, so
    `configured()` reports False until one exists — see LiveMerchantProvider.
    """
    id = "live:booking"
    family = "booking"
    mode = "live"
    label = "HTTP Booking API (live)"
    serves = ("*",)

    @staticmethod
    def configured() -> bool:
        return False

    def do_retrieve(self, p: dict) -> ProviderResult:
        return ProviderResult(
            False, "error", self.id, self.mode,
            message="Live booking retrieval is not implemented yet.",
            error_code=ErrorCode.TOOL_UNAVAILABLE, retryable=False)


class LivePaymentProvider(Provider):
    """Live payment disputes.

    Not yet implemented: there is no real HTTP call behind this class, so
    `configured()` reports False until one exists — see LiveMerchantProvider.
    """
    id = "live:payment"
    family = "payment"
    mode = "live"
    label = "Live Payment Gateway"
    serves = ("*",)

    @staticmethod
    def configured() -> bool:
        return False

    def do_escalate(self, p: dict) -> ProviderResult:
        return ProviderResult(
            False, "error", self.id, self.mode,
            message="Live payment dispute escalation is not implemented yet.",
            error_code=ErrorCode.TOOL_UNAVAILABLE, retryable=False)


# Providers whose `configured()` is checked at bootstrap; add new live providers
# here rather than hand-wiring them into __init__.py's bootstrap().
ALL = (LiveEmailProvider, LiveBrowserProvider, LiveMerchantProvider, LiveBookingProvider, LivePaymentProvider)
