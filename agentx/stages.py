"""
Stage-aware tracks — what a case is doing now, and what is worth doing about it.

`case.STATE_COPY` already says what a state IS ("Waiting on them"). It does not
say what the person should DO about it, and those are different questions. Someone
whose case sits at WAITING_EXTERNAL wants to know whether to chase, when chasing
becomes reasonable, and what happens if the deadline passes — and the answer is
different at ESCALATED, where chasing again is the wrong move.

So each state declares a TRACK: what Agent X is doing, what the user can do, which
chat goals are worth using here, and what comes next. That last part is derived
from `case.TRANSITIONS` rather than restated, so a state that gains a transition
cannot end up with a track that describes the old state machine.

WHAT IS DECLARED AND WHAT IS DERIVED

    declared    the guidance — what Agent X is doing, what you can do
    derived     the next states (from TRANSITIONS), the active goals (checked
                against the mode catalogue), the alerts (from live case rows)

Nothing here invents a number. Elapsed-time expectations come from the case's own
deadlines, and where a duration is quoted from history it is labelled as sandbox
measurement, which is what it is.

ALERTS ARE COMPUTED, NEVER CANNED

`attention()` reads the case's real deadlines, open questions and pending
approvals. The source project this pattern came from shipped a table of hardcoded
weather warnings keyed by district; the pattern is worth having and the fabricated
data is not, so alerts here are only ever things that are actually true of a row
in the database.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from agentx.case import STATE_COPY, TRANSITIONS
from agentx.ontology import TERMINAL_STATES

# Alert severities, in the order a person should read them.
URGENT, SOON, INFO = "urgent", "soon", "info"
_SEVERITY_ORDER = {URGENT: 0, SOON: 1, INFO: 2}

# A deadline this close is urgent; beyond it, worth flagging but not alarming.
URGENT_DAYS = 3.0
SOON_DAYS = 10.0


@dataclass(frozen=True)
class Track:
    """What to do while a case is in one state."""
    state: str
    doing: str                                   # what Agent X is doing
    you_can: tuple[str, ...] = ()                # what the user can do now
    goals: tuple[str, ...] = ()                  # chat goals worth using here
    waiting_on: str = "agent"                    # agent | you | them | nobody

    def as_dict(self) -> dict:
        copy = STATE_COPY.get(self.state, {})
        return {
            "state": self.state,
            "label": copy.get("label", self.state),
            "detail": copy.get("detail", ""),
            "doing": self.doing,
            "you_can": list(self.you_can),
            "goals": list(self.goals),
            "waiting_on": self.waiting_on,
            # Derived, never restated — see the module docstring.
            "next_states": [s for s in TRANSITIONS.get(self.state, ())
                            if s not in ("WITHDRAWN",)],
            "terminal": self.state in TERMINAL_STATES,
        }


_TRACKS: tuple[Track, ...] = (
    Track("OPEN", waiting_on="agent",
          doing="Reading what you wrote and working out which problem this is.",
          you_can=("Add anything else you remember",
                   "Attach a receipt, statement or screenshot"),
          goals=("verify_booking",)),
    Track("INVESTIGATING", waiting_on="agent",
          doing="Establishing the facts, checking which rules apply, and working "
                "out what you are owed.",
          you_can=("Attach evidence — it usually moves this fastest",),
          goals=("hidden_fees", "refund_policy")),
    Track("NEEDS_INPUT", waiting_on="you",
          doing="Nothing until you answer — it would rather ask than guess.",
          you_can=("Answer the open question",
                   "Attach the missing document",
                   "Say you do not have it, and Agent X will work around it"),
          goals=("verify_booking", "hidden_fees")),
    Track("ACTION_REQUIRED", waiting_on="you",
          doing="Holding a prepared action until you approve it.",
          you_can=("Review the draft and approve it",
                   "Change the remedy if another route suits you better",
                   "Decline — nothing is sent without you"),
          goals=("dispute_letter", "what_its_worth")),
    Track("ACTION_SUBMITTED", waiting_on="them",
          doing="The action has gone out. Watching for a reply.",
          you_can=("Forward anything they send you",),
          goals=("what_its_worth",)),
    Track("WAITING_EXTERNAL", waiting_on="them",
          doing="Waiting out their stated deadline, then chasing automatically.",
          you_can=("Forward their reply if one arrives",
                   "Ask what happens when the deadline passes"),
          goals=("what_its_worth", "escalation_route")),
    Track("FOLLOW_UP_REQUIRED", waiting_on="agent",
          doing="Their deadline passed. Preparing a chase.",
          you_can=("Approve the chase",
                   "Escalate now instead of chasing again"),
          goals=("escalation_route", "what_its_worth")),
    Track("ESCALATED", waiting_on="them",
          doing="Taken above the first line — to the regulator, ombudsman or "
                "platform. Chasing the original contact again is no longer the "
                "right move.",
          you_can=("Forward anything the higher authority sends",
                   "Check what that authority can and cannot order"),
          goals=("escalation_route",)),
    Track("RESOLVED", waiting_on="nobody",
          doing="Done. The outcome was verified against their records, not their "
                "reply, and the receipt is signed.",
          you_can=("Download the signed receipt",
                   "Verify it yourself at /verify",
                   "Erase the case — the receipt stays checkable"),
          goals=()),
    Track("CLOSED_UNRESOLVED", waiting_on="nobody",
          doing="Closed without the remedy. What was tried is still on the record.",
          you_can=("Read what blocked it",
                   "Open a new case if something changes",
                   "Check whether a regulator will still take it"),
          goals=("escalation_route",)),
    Track("WITHDRAWN", waiting_on="nobody",
          doing="Withdrawn at your request. Nothing further will be sent.",
          you_can=("Erase it, with proof",),
          goals=()),
)

TRACKS: dict[str, Track] = {t.state: t for t in _TRACKS}


def track(state: str | None) -> dict | None:
    """The track for a case state, or None for an unknown state."""
    t = TRACKS.get((state or "").strip().upper())
    return t.as_dict() if t else None


def catalogue() -> list[dict]:
    """Every stage track, in state-machine order — for docs and the UI."""
    return [t.as_dict() for t in _TRACKS]


# ─────────────────────────────────────────────────────────────────────────────
# proactive attention
# ─────────────────────────────────────────────────────────────────────────────
def _days_left(deadline: dict) -> float | None:
    """How long is left on a deadline, on the clock the CASE lives on.

    Read from the row rather than recomputed from wall-clock time, and that is
    not a shortcut — sandbox cases run against a movable clock (`sandbox_clock`)
    so a seven-day chase can happen in a second. Subtracting `now()` from a
    deadline would measure the clock's displacement instead of the case's own
    remaining time, and every sandbox case would show its deadlines as long
    passed. `case.deadlines()` already does this arithmetic correctly.
    """
    value = deadline.get("days_left")
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def attention(snapshot: dict) -> list[dict]:
    """What needs a person's attention on this case, most urgent first.

    Computed from the case's own rows — deadlines, unanswered questions, pending
    approvals. Every alert names the thing it is about, so none of them can be
    true in general and false for this case.
    """
    case = snapshot.get("case") or {}
    state = (case.get("state") or "").upper()
    if state in TERMINAL_STATES:
        return []

    out: list[dict] = []

    for deadline in snapshot.get("deadlines") or []:
        if str(deadline.get("status", "")).lower() in ("met", "closed", "cancelled"):
            continue
        days = _days_left(deadline)
        if days is None:
            continue
        label = deadline.get("label") or deadline.get("kind") or "a deadline"
        if deadline.get("overdue") or days < 0:
            out.append({"severity": URGENT, "kind": "deadline_passed",
                        "message": f"{label} passed {abs(days):.0f} day(s) ago.",
                        "advice": "Agent X treats a missed deadline as grounds to "
                                  "chase or escalate — check the plan.",
                        "days": days})
        elif days <= URGENT_DAYS:
            out.append({"severity": URGENT, "kind": "deadline_near",
                        "message": f"{label} is {days:.0f} day(s) away.",
                        "advice": "Anything you still need to supply should go in now.",
                        "days": days})
        elif days <= SOON_DAYS:
            out.append({"severity": SOON, "kind": "deadline_ahead",
                        "message": f"{label} is in {days:.0f} day(s).",
                        "advice": "No action needed yet.", "days": days})

    questions = snapshot.get("questions") or []
    if questions:
        out.append({"severity": URGENT if state == "NEEDS_INPUT" else SOON,
                    "kind": "question_open",
                    "message": f"{len(questions)} question(s) waiting on you.",
                    "advice": questions[0].get("question")
                              or "Answering unblocks the case."})

    approvals = snapshot.get("approvals") or []
    if approvals:
        out.append({"severity": URGENT, "kind": "approval_pending",
                    "message": f"{len(approvals)} action(s) need your go-ahead.",
                    "advice": "Nothing is sent until you approve it."})

    for contradiction in snapshot.get("contradictions") or []:
        if str(contradiction.get("severity", "")).lower() in ("blocking", "high"):
            out.append({"severity": URGENT, "kind": "contradiction",
                        "message": "Two pieces of evidence disagree on "
                                   f"{contradiction.get('predicate') or 'a fact'}.",
                        "advice": "Agent X will not act on a contested figure "
                                  "until this is settled."})

    out.sort(key=lambda a: (_SEVERITY_ORDER.get(a["severity"], 3),
                            a.get("days") if a.get("days") is not None else 999))
    return out


def briefing(snapshot: dict) -> dict:
    """Stage track plus live alerts — the whole 'where am I, what now' answer."""
    case = snapshot.get("case") or {}
    alerts = attention(snapshot)
    return {
        "track": track(case.get("state")),
        "alerts": alerts,
        "needs_you": any(a["severity"] == URGENT for a in alerts),
    }
