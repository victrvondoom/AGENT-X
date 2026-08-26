"""
Provider interfaces — the boundary between resolution logic and the outside world.

The resolution engine must never know whether a refund request went to a real
merchant API, a browser automation session, or a sandbox. It knows only that it
asked a `MerchantProvider` to `request_refund` and got a `ProviderResult` back.
That is what lets a new integration be added without touching the planner, and
what lets the whole system be tested deterministically.

TWO PROPERTIES THAT ARE NOT OPTIONAL

**Mode is carried, never inferred.** Every provider declares `mode` as `sandbox`
or `live`, and it travels on every result, into the execution record, into the
case chain, and onto the resolution receipt. A user reading "refund requested"
can always see which world it happened in. This is the mechanism behind the
product rule that Agent X never fakes an integration: a sandbox action is not
disguised as a real one, it is labelled as what it is.

**Evidence, not assertion.** A provider does not return "success". It returns what
the external system said — a status, a reference, and a captured artefact
(response body, confirmation page, screenshot). The verification step then
RE-READS the external system to confirm the state actually changed. A provider
that returns only a boolean cannot support that pattern, so `ProviderResult`
does not have one.

FAILURE IS A FIRST-CLASS RESULT

`ok=False` with a reason is a normal outcome, not an exception. Merchants refuse
refunds, forms reject submissions, sites go down. The planner has branches for
those; raising would collapse a branch into a stack trace.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

# Every provider belongs to exactly one family. A family is what a capability
# declares a dependency on, so adding a provider means registering it under an
# existing family — never editing the planner.
FAMILIES = ("merchant", "booking", "payment", "subscription", "telecom",
            "email", "browser", "document")


class ErrorCode:
    """The operational-failure vocabulary — what KIND of not-ok this was, not just
    that it wasn't ok. `outcome` above already distinguishes "the call worked and
    the answer was no" from "the call didn't work"; `error_code` exists for the
    second half, to say what a caller should DO about it rather than leaving that
    to a free-text `message`.

    Deliberately not one code per HTTP status: `execution/retry.py` only needs to
    ask two questions — is this worth trying again, and if so, when — and that
    collapses cleanly onto this set without a code per status.
    """
    AUTH_REQUIRED = "auth_required"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    TOOL_UNAVAILABLE = "tool_unavailable"
    INVALID_INPUT = "invalid_input"
    PERMISSION_DENIED = "permission_denied"
    EXTERNAL_REJECTED = "external_rejected"
    CONFLICT = "conflict"
    RETRYABLE = "retryable"            # transient, cause otherwise unclassified
    NON_RETRYABLE = "non_retryable"    # permanent, cause otherwise unclassified
    UNKNOWN_FAILURE = "unknown_failure"
    REQUIRES_USER = "requires_user"


ERROR_CODES = tuple(v for k, v in vars(ErrorCode).items() if not k.startswith("_"))

# Codes a retry can plausibly fix. Everything else means retrying would either
# repeat a mistake (INVALID_INPUT), ask again for something a person has to grant
# (AUTH_REQUIRED, PERMISSION_DENIED, REQUIRES_USER), or restate a decision the
# counterparty already made (EXTERNAL_REJECTED, NON_RETRYABLE, CONFLICT).
RETRYABLE_CODES = (ErrorCode.RETRYABLE, ErrorCode.TIMEOUT, ErrorCode.RATE_LIMITED)


@dataclass
class ProviderResult:
    """What an external system actually said.

    `ok` means the CALL completed as expected, not that the user got what they
    wanted. A merchant that refuses a refund returns ok=True with
    outcome='refused' — the interaction worked, the answer was no. A timeout
    returns ok=False. Collapsing those two would make "we could not reach them"
    indistinguishable from "they said no", which are opposite next steps.
    """
    ok: bool
    outcome: str                       # accepted | refused | pending | not_found | error | done
    provider: str
    mode: str                          # sandbox | live
    external_ref: str | None = None
    message: str = ""                  # safe to show a user
    data: dict = field(default_factory=dict)
    evidence_text: str | None = None   # what to store as captured evidence
    evidence_kind: str | None = None
    responds_in_days: float | None = None   # when the counterparty says it will reply
    retryable: bool = False
    error_code: str | None = None      # one of ErrorCode.* when ok is False
    retry_after: float | None = None   # seconds; provider-supplied, overrides backoff
    technical_detail: str | None = None  # exception text etc. — never shown to a user
    request_id: str | None = None      # the provider's own correlation id, if it has one

    def __post_init__(self):
        # error_code is the source of truth for retryability once set — a caller
        # that only checks `retryable` on an old-style result (no error_code) still
        # gets the right answer, so the field can be adopted provider-by-provider
        # rather than requiring every do_* method to be rewritten at once.
        if self.error_code and not self.ok:
            self.retryable = self.retryable or self.error_code in RETRYABLE_CODES

    def as_dict(self) -> dict:
        d = asdict(self)
        # Captured evidence can be large; the execution record links to the stored
        # evidence item rather than carrying a copy of it in the result JSON.
        d.pop("evidence_text", None)
        return d

    def user_dict(self) -> dict:
        """The subset safe to hand to an API response or render in the UI.

        `technical_detail` (raw exception text, provider internals) stays out —
        it belongs in `as_dict()`'s output for the stored execution record, where
        a developer reading the database can see it, never in a response a
        consumer-facing client renders.
        """
        d = self.as_dict()
        d.pop("technical_detail", None)
        return d


class ProviderError(RuntimeError):
    """Raised only for programming errors — an unknown action, a bad argument.

    Never for a business outcome, and never for an outage: both of those are
    ProviderResults, because both are things a plan branch has to be able to
    reason about.
    """


class Provider:
    """Base class. Subclasses declare a family, an id, a mode, and their actions."""

    id: str = "abstract"
    family: str = "merchant"
    mode: str = "sandbox"
    label: str = "Abstract provider"
    # Which counterparties this provider can act for. "*" means any.
    serves: tuple[str, ...] = ("*",)

    # A live connection to whatever store the provider needs, handed over by the
    # runner for the duration of one action. Providers are stateless between
    # calls on purpose: a provider that holds a connection across executions is a
    # provider that outlives the transaction it was supposed to be inside.
    conn = None

    def bind(self, conn):
        self.conn = conn
        return self

    def supports(self, action: str) -> bool:
        return hasattr(self, f"do_{action}")

    def actions(self) -> list[str]:
        return sorted(n[3:] for n in dir(self) if n.startswith("do_"))

    def can_serve(self, counterparty: str | None) -> bool:
        if "*" in self.serves:
            return True
        if not counterparty:
            return False
        c = counterparty.strip().lower()
        return any(c == s.lower() or s.lower() in c for s in self.serves)

    def execute(self, action: str, params: dict) -> ProviderResult:
        fn = getattr(self, f"do_{action}", None)
        if fn is None:
            raise ProviderError(
                f"{self.id} cannot perform {action!r}; it supports {self.actions()}")
        return fn(params)

    def describe(self) -> dict:
        return {"id": self.id, "family": self.family, "mode": self.mode,
                "label": self.label, "actions": self.actions(),
                "serves": list(self.serves)}


class UnavailableProvider(Provider):
    """A stand-in for a family nothing is registered for.

    It exists so the failure is legible. Without it, an unserved family shows up
    as a KeyError somewhere in the executor; with it, the planner sees an explicit
    "no provider for this family" and routes around the capability instead of
    emitting a step that cannot run.
    """

    mode = "none"

    def __init__(self, family: str, reason: str):
        self.id = f"unavailable:{family}"
        self.family = family
        self.label = f"No provider registered for {family}"
        self._reason = reason

    def actions(self) -> list[str]:
        return []

    def supports(self, action: str) -> bool:
        return False

    def execute(self, action: str, params: dict) -> ProviderResult:
        return ProviderResult(
            ok=False, outcome="error", provider=self.id, mode="none",
            message=f"No {self.family} provider is configured, so {action!r} cannot "
                    f"run. {self._reason}")
