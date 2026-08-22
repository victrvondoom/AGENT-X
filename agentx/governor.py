"""
The Risk & Autonomy Governor — what Agent X may do without being asked.

An agent that acts in the world on someone's behalf needs a boundary that is
declared, not emergent. The failure this module prevents is the one that ends a
consumer agent product: it files a chargeback against a subscription the user
simply forgot renewing, and the user's card relationship with that merchant is
over. Nothing about that action is technically wrong; it is a governance failure.

FIVE LEVELS, AND THE LINE THAT MATTERS

    0  Information only.        Nothing leaves Agent X. Reading, explaining.
    1  Analysis and advice.     Retrieval and reasoning; the user acts.
    2  Prepare, then confirm.   Agent X drafts and stages; a human presses go.
    3  Execute low-risk,        Reversible actions run unattended under a
       reversible actions.      standing grant, and are reported afterwards.
    4  Autonomous within        Irreversible actions run under an explicit,
       a written policy.        bounded standing authorisation.

The line that matters is between 2 and 3, and it is not about how clever the agent
is. It is about REVERSIBILITY. A refund request that a merchant declines costs the
user nothing; a chargeback, a cancellation, or a regulator complaint cannot be
withdrawn once filed. So reversibility is a first-class property of every
capability and every action, and irreversible actions carry a hard floor no case
level can lower.

FOUR HARD RULES THAT OVERRIDE THE LEVEL ENTIRELY

  1. An irreversible, high-risk action ALWAYS requires an explicit, action-specific
     authorisation — even at level 4. A standing grant can pre-approve a class of
     action; it cannot pre-approve this one.
  2. A blocking contradiction stops every action that depends on the contested
     value. Agent X does not act on a number two documents disagree about.
  3. Confidence below the floor for the action's risk class blocks it. The floors
     come from the same declared-policy discipline `core/trust/gate.py` uses.
  4. A money action above the authorised ceiling is refused, not truncated.

Every decision returns the rule that produced it, and that string is what appears
on the approval card and in the signed receipt. A user who wants to know why they
were asked gets the actual reason, not a category.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

from agentx import normalize

LEVELS: dict[int, dict] = {
    0: {"name": "Information only",
        "means": "Agent X reads and explains. Nothing leaves this device.",
        "allows": "understanding, document reading, policy explanation"},
    1: {"name": "Analysis and recommendation",
        "means": "Agent X investigates and tells you what it would do. You act.",
        "allows": "retrieval from providers, eligibility analysis, drafting"},
    2: {"name": "Prepare and confirm",
        "means": "Agent X prepares each action and shows it to you before sending.",
        "allows": "everything at level 1, plus sending what you approve"},
    3: {"name": "Act on reversible things",
        "means": "Agent X sends reversible requests without stopping to ask, and "
                 "tells you afterwards. Anything permanent still waits for you.",
        "allows": "refund requests, follow-ups, retrieval, verification"},
    4: {"name": "Autonomous under a written policy",
        "means": "Agent X may also take permanent actions inside limits you set — "
                 "an amount ceiling, an expiry, and named action types.",
        "allows": "cancellation and escalation within the policy; disputes still "
                  "need their own explicit approval"},
}

# Confidence floors by risk class. Declared, not implicit, so a judge or a user can
# read the rule that gated an action rather than infer it from behaviour.
CONFIDENCE_FLOOR = {"low": 0.55, "medium": 0.70, "high": 0.85}

def _irreversible_actions() -> frozenset[str]:
    """Which verbs cannot be taken back, read from the ACTION VOCABULARY itself.

    This used to be a hardcoded set here, which made `agentx/execution/actions.py`
    and this module two sources of truth for the same property — and they drifted:
    `navigate` is declared `reversible=False` there and was absent from the set
    here, so driving a counterparty's web form counted as reversible for
    governance purposes and could run unattended at level 3. Deriving it from the
    single declaration removes the class of bug, not just that instance.
    """
    from agentx.execution.actions import ACTIONS
    return frozenset(v for v, spec in ACTIONS.items() if not spec.reversible)

# Actions that always need their own approval, whatever standing grant exists.
ALWAYS_EXPLICIT = {"escalate"}


def _writes_externally(action: str) -> bool:
    """Does this verb send something out of Agent X?

    Imported lazily. `agentx.execution` pulls in the runner, and the runner depends
    on this module — a top-level import here would close the cycle. The lookup
    happens once per assessment, long after both modules exist.
    """
    from agentx.execution.actions import ACTIONS
    spec = ACTIONS.get(action)
    return bool(spec and spec.writes_externally)


# Default ceiling on a single money action under a standing grant, in minor units.
# Deliberately conservative: a standing authorisation that quietly covers a
# four-figure action is not a standing authorisation the user understood.
DEFAULT_CEILING_MINOR = 25_000


@dataclass
class Verdict:
    allow: bool
    requires_authorization: bool
    level_required: int
    level_granted: int
    rule: str
    explain: str
    risk: str
    reversible: bool
    prompt: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _fmt_level(n: int) -> str:
    lv = LEVELS.get(n, {})
    return f"level {n} ({lv.get('name', 'unknown')})"


def assess(*, action: str, capability, case_level: int, risk: str,
           confidence: float | None, amount_minor: int | None = None,
           currency: str | None = None, blocking_contradictions: int = 0,
           standing_grant: dict | None = None,
           counterparty: str | None = None) -> Verdict:
    """Decide whether this action may proceed, and on whose authority.

    `capability` may be a Capability or None (for a bare action). Everything else
    is explicit rather than read out of a case object, so the governor can be
    tested exhaustively without a database — which is the only way a rule set this
    consequential can be trusted.
    """
    irreversible = _irreversible_actions()
    reversible = not (action in irreversible or (capability is not None
                                                 and not capability.reversible))
    required = max(int(getattr(capability, "required_level", 1) or 1),
                   {"low": 1, "medium": 2, "high": 3}.get(risk, 2))
    grant = standing_grant or {}

    # Rule 2 first: a contested fact poisons everything downstream of it, and
    # checking it after the level would let a high-autonomy case act on a number
    # two documents disagree about.
    if blocking_contradictions and action not in ("inspect", "retrieve", "draft"):
        return Verdict(False, True, required, case_level, "blocking_contradiction",
                       f"{blocking_contradictions} unresolved contradiction(s) between "
                       f"sources mean the amount or reference this action depends on is "
                       f"not established. Agent X will not act on a disputed value.",
                       risk, reversible)

    # Rule 3: confidence floor by risk class.
    floor = CONFIDENCE_FLOOR.get(risk, 0.7)
    if confidence is None:
        return Verdict(False, True, required, case_level, "no_confidence_signal",
                       "No confidence was computed for this case. Absent is not the "
                       "same as high, so this is routed to you rather than assumed.",
                       risk, reversible)
    if float(confidence) < floor and action not in ("inspect", "retrieve", "draft"):
        return Verdict(False, True, required, case_level, "below_confidence_floor",
                       f"Case confidence {float(confidence):.2f} is below the "
                       f"{floor:.2f} required for a {risk}-risk action.",
                       risk, reversible)

    # Rule 1: irreversible + high risk is never covered by a level alone.
    if action in ALWAYS_EXPLICIT or (not reversible and risk == "high"):
        return Verdict(True, True, max(required, 4), case_level, "irreversible_high_risk",
                       "This cannot be undone once it is filed, so it needs your "
                       "explicit approval for this specific action — a standing "
                       "permission does not cover it.",
                       risk, reversible,
                       prompt=_prompt(action, amount_minor, currency, counterparty,
                                      reversible))

    # Rule 4: money ceiling on anything running under a standing grant.
    ceiling = int(grant.get("ceiling_minor") or DEFAULT_CEILING_MINOR)
    if amount_minor and case_level >= 3 and int(amount_minor) > ceiling:
        return Verdict(False, True, required, case_level, "above_amount_ceiling",
                       f"{normalize.fmt_money(amount_minor, currency)} is above the "
                       f"{normalize.fmt_money(ceiling, currency)} ceiling on automatic "
                       f"actions, so this one comes back to you.",
                       risk, reversible,
                       prompt=_prompt(action, amount_minor, currency, counterparty,
                                      reversible))

    # The line between level 2 and level 3. Below 3, anything that leaves Agent X
    # is prepared and shown, never sent — that is what "prepare and confirm" means,
    # and reading it off the level rather than the risk class is what stops a
    # low-risk-but-outbound action from slipping past a user who asked to confirm.
    if case_level <= 2 and _writes_externally(action):
        return Verdict(True, True, max(required, 3), case_level, "confirm_before_sending",
                       f"You are at {_fmt_level(case_level)}, where Agent X prepares "
                       f"outbound actions and waits for you. Raise the case to level 3 "
                       f"to let reversible actions go automatically.",
                       risk, reversible,
                       prompt=_prompt(action, amount_minor, currency, counterparty,
                                      reversible))

    if case_level >= required and (reversible or case_level >= 4):
        return Verdict(True, False, required, case_level, "within_granted_autonomy",
                       f"You granted {_fmt_level(case_level)}; this action needs "
                       f"{_fmt_level(required)} and is "
                       f"{'reversible' if reversible else 'permanent but inside your policy'}.",
                       risk, reversible)

    return Verdict(True, True, required, case_level, "above_granted_autonomy",
                   f"This action needs {_fmt_level(required)} and the case is at "
                   f"{_fmt_level(case_level)}, so Agent X has prepared it and is asking.",
                   risk, reversible,
                   prompt=_prompt(action, amount_minor, currency, counterparty, reversible))


def _prompt(action: str, amount_minor: int | None, currency: str | None,
            counterparty: str | None, reversible: bool) -> str:
    """The sentence the user is shown, stored verbatim with their answer.

    Written to be readable by someone who has not been following along: what will
    happen, to whom, for how much, and whether it can be undone. An approval whose
    prompt cannot be reconstructed later is not evidence of consent.
    """
    # (verb, preposition). The preposition is part of the verb: you ask a company
    # FOR a refund and send an email TO it, and an approval prompt that reads
    # "ask for a refund to Kartly" makes a consequential question look careless.
    verb, prep = {
        "request_refund": ("ask for a refund", "from"),
        "cancel": ("cancel this service", "with"),
        "escalate": ("escalate this case", "to"),
        "email": ("send an email", "to"),
        "submit_form": ("submit a form", "to"),
        "navigate": ("use the website", "of"),
        "draft": ("prepare a message", "for"),
        "follow_up": ("chase for a response", "from"),
        "verify": ("check the outcome", "with"),
    }.get(action, (action.replace("_", " "), "with"))
    who = f" {prep} {counterparty}" if counterparty else ""
    amt = f" for {normalize.fmt_money(amount_minor, currency)}" if amount_minor else ""
    tail = ("You can undo this if it goes wrong."
            if reversible else "This cannot be undone once it is sent.")
    return f"Agent X wants to {verb}{who}{amt}. {tail}"


def describe_levels() -> list[dict]:
    """For the UI: the ladder, in the user's language."""
    return [{"level": n, **v} for n, v in sorted(LEVELS.items())]


def policy_snapshot() -> dict:
    """The exact rules in force, for the audit chain and the receipt.

    Recorded with every governed decision for the same reason
    `core/trust/gate.py` records its thresholds: a user or a regulator must be
    able to re-derive why an action was allowed months later without this binary.
    """
    return {
        "levels": {str(k): v["name"] for k, v in LEVELS.items()},
        "confidence_floors": CONFIDENCE_FLOOR,
        "irreversible_actions": sorted(_irreversible_actions()),
        "always_explicit": sorted(ALWAYS_EXPLICIT),
        "default_ceiling_minor": DEFAULT_CEILING_MINOR,
        "hard_rules": [
            "below level 3, anything that leaves Agent X is prepared and confirmed",
            "irreversible + high risk requires explicit per-action authorisation",
            "a blocking contradiction stops any action depending on the contested value",
            "confidence below the risk-class floor blocks the action",
            "an amount above the authorised ceiling is refused, not truncated",
        ],
    }
