"""
The standardised action vocabulary.

Thirteen verbs. Every plan step, every provider method, every execution record and
every approval card is expressed in them, and nothing else. The constraint is the
value: a fixed vocabulary is what lets the governor reason about risk without
knowing which vertical it is in, lets a plan be validated before it runs, and lets
a receipt be read by someone who has never seen this system.

    search        find candidate records
    inspect       read one record or page without changing anything
    retrieve      pull a record into the case as evidence
    draft         compose a message; nothing leaves Agent X
    email         send a message to a counterparty
    submit_form   complete and submit a structured form
    navigate      drive a web session
    cancel        end a service, plan or booking
    request_refund   ask for money back through the counterparty's own channel
    escalate      move the case to a higher authority
    schedule      set a deadline or a future check
    follow_up     chase an outstanding response
    verify        re-read the external system to confirm a claimed state

Two properties are declared per verb rather than decided at the call site, because
both are consequential and both must be visible to whoever approves the action:

  `reversible`  can this be taken back if it was wrong?
  `risk`        what class of harm does getting it wrong cause?

`writes_externally` is the honest dividing line the UI uses: below it, Agent X is
reading and thinking; above it, something has left the building.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class ActionSpec:
    verb: str
    label: str
    summary: str
    risk: str                 # low | medium | high
    reversible: bool
    writes_externally: bool
    family: str | None        # provider family required, None = internal
    produces_evidence: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


_SPECS: tuple[ActionSpec, ...] = (
    ActionSpec("search", "Search", "Look for candidate records at a counterparty.",
               "low", True, False, "any"),
    ActionSpec("inspect", "Inspect", "Read a record or page without changing it.",
               "low", True, False, None),
    ActionSpec("retrieve", "Retrieve", "Pull an external record into the case as evidence.",
               "low", True, False, "any", produces_evidence=True),
    ActionSpec("draft", "Draft", "Compose a message. Nothing is sent.",
               "low", True, False, None),
    ActionSpec("email", "Send email", "Send a message to the counterparty.",
               "medium", False, True, "email", produces_evidence=True),
    ActionSpec("submit_form", "Submit form", "Complete and submit a structured claim form.",
               "medium", False, True, "browser", produces_evidence=True),
    ActionSpec("navigate", "Use the website", "Drive a self-service flow on the counterparty's site.",
               "medium", False, True, "browser", produces_evidence=True),
    ActionSpec("cancel", "Cancel", "End a subscription, plan or booking.",
               "medium", False, True, "subscription", produces_evidence=True),
    ActionSpec("request_refund", "Request refund",
               "Ask the counterparty for money back through its own channel.",
               "medium", True, True, "merchant", produces_evidence=True),
    ActionSpec("escalate", "Escalate",
               "Take the case to a supervisor, platform, issuer or regulator.",
               "high", False, True, "any", produces_evidence=True),
    ActionSpec("schedule", "Schedule", "Record a deadline or a future check.",
               "low", True, False, None),
    ActionSpec("follow_up", "Follow up", "Chase an outstanding response.",
               "low", True, True, "email", produces_evidence=True),
    ActionSpec("verify", "Verify",
               "Re-read the external system to confirm a state actually changed.",
               "low", True, False, "any", produces_evidence=True),
)

ACTIONS: dict[str, ActionSpec] = {a.verb: a for a in _SPECS}

# The lifecycle every execution record walks. Terminal states are final: a retry
# is a NEW record, never a resurrected one, so a failure can never be edited out
# of the history.
EXECUTION_STATES = ("REQUESTED", "AUTHORIZED", "STARTED", "COMPLETED", "FAILED",
                    "REFUSED")
TERMINAL_EXECUTION_STATES = ("COMPLETED", "FAILED", "REFUSED")


def spec(verb: str) -> ActionSpec | None:
    return ACTIONS.get(verb)


def is_external(verb: str) -> bool:
    s = ACTIONS.get(verb)
    return bool(s and s.writes_externally)


def catalogue() -> list[dict]:
    return [a.as_dict() for a in _SPECS]
