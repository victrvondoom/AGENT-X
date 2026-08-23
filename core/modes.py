"""
Answer modes — the "Select Goal" options, declared once.

The chat offers a goal picker: Research, Analyze, Decide, and a consumer group
(Verify Booking, Hidden Fees, Refund Policy, …). Until now those options lived in
the HTML and their behaviour lived in an if/elif chain in `ask.py`, and the two
had already drifted: **four of the five consumer options did nothing at all.**
Clicking "Hidden Fees" sent `capability=hidden_fees`, fell past every branch, and
produced the default answer. The button worked; the feature did not.

So the list lives here, once, and both sides read it. The UI is built from
`catalogue()` and `ask()` routes from the same dict, which makes the drift that
caused that bug structurally impossible rather than merely fixed.

THREE KINDS OF TRACK

Most modes are prompt shaping — they change how the same retrieved context is
written up. Four are GROUNDED: they attach real retrieved material and are marked
as such in the picker.

    escalation_route   the real complaint ladder from the regulatory corpus
                       (`agentx/knowledge`)
    case_status        where each open case actually stands (`agentx/stages`)
    needs_attention    only what genuinely needs the user, across every case
    what_its_worth     what claims like this have actually recovered
                       (`agentx/outcomes`)

Every one of them refuses rather than invents. A mode that answers "who do I
escalate to" from a model's memory of banking regulation is worse than one that
says the corpus has nothing — the first is confidently wrong to someone about to
spend a week writing to the wrong office. `case_status` will not describe a case
that is not in the list, and `needs_attention` will not manufacture a task to seem
useful.

The third kind is an INPUT track: dictation. It carries no prompt and never
reaches `ask()` — it changes how the user asks, not how Agent X answers — but it
is listed in the picker because that is where someone looks to find out what this
thing can do.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

GENERAL = "general"
INPUT = "input"
CONSUMER = "consumer"


@dataclass(frozen=True)
class Mode:
    """One selectable goal."""
    id: str
    label: str
    group: str
    prompt: str
    temperature: float = 0.0
    deep_retrieval: bool = False
    # Optional grounded context, resolved at answer time. Returns text to append
    # to the prompt context, or None when it has nothing — never a placeholder.
    context: Callable[[str, str], str | None] | None = None

    def as_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "group": self.group,
                "grounded": self.context is not None}


# ─────────────────────────────────────────────────────────────────────────────
# grounded context providers
# ─────────────────────────────────────────────────────────────────────────────
def _escalation_context(query: str, workspace: str) -> str | None:
    """The real complaint ladder for whatever the user is asking about.

    Uses the same deterministic retrieval a case's research step uses, so the
    ombudsman named here is one that appears in a checked-in published source
    rather than one the model remembers. Returns None when the corpus does not
    cover the subject, which is common and is the honest answer.
    """
    try:
        from agentx import knowledge
    except Exception:
        return None
    hits = knowledge.search(query, limit=3)
    if not hits:
        return None
    blocks = [f"[{h['citation']}]\n{h['text']}" for h in hits]
    return ("Published escalation guidance retrieved for this question. Use ONLY "
            "these routes and name their source; if they do not answer the "
            "question, say so.\n\n" + "\n\n".join(blocks))


def _case_status_context(query: str, workspace: str) -> str | None:
    """Where the user's live cases stand, and what each is waiting on.

    The stage-track idea, answered from real rows: for every open case, the state
    it is in, who it is waiting on, and what the user can do about it. Returns
    None when there are no open cases, so the mode says "you have none" instead
    of describing a case that does not exist.
    """
    try:
        from agentx import case as case_mod, engine, stages, store as axstore
    except Exception:
        return None
    try:
        axstore.ensure_schema()
        with axstore.connect() as conn:
            rows = case_mod.list_cases(conn, workspace=workspace, limit=6)
            blocks = []
            for row in rows:
                snap = engine.snapshot(conn, row["id"])
                brief = snap.get("briefing") or {}
                t = brief.get("track") or {}
                if t.get("terminal"):
                    continue
                lines = [f"Case {row['id']} — {row.get('title') or 'untitled'}",
                         f"  Stage: {t.get('label')} ({t.get('state')})",
                         f"  Agent X is: {t.get('doing')}",
                         f"  Waiting on: {t.get('waiting_on')}"]
                if t.get("you_can"):
                    lines.append("  You can: " + "; ".join(t["you_can"]))
                for alert in (brief.get("alerts") or [])[:3]:
                    lines.append(f"  [{alert['severity']}] {alert['message']} "
                                 f"{alert.get('advice', '')}".rstrip())
                blocks.append("\n".join(lines))
    except Exception:
        return None

    if not blocks:
        return ("The user has no open cases. Say so plainly and offer to open one "
                "— do not describe a case that does not exist.")
    return ("The user's live cases, with the stage each is at and what it is "
            "waiting on. Answer ONLY from these:\n\n" + "\n\n".join(blocks))


def _attention_context(query: str, workspace: str) -> str | None:
    """Only the things that actually need the user, across every open case."""
    try:
        from agentx import case as case_mod, engine, store as axstore
    except Exception:
        return None
    try:
        axstore.ensure_schema()
        with axstore.connect() as conn:
            urgent = []
            for row in case_mod.list_cases(conn, workspace=workspace, limit=10):
                brief = (engine.snapshot(conn, row["id"]).get("briefing") or {})
                for alert in brief.get("alerts") or []:
                    if alert["severity"] in ("urgent", "soon"):
                        urgent.append(f"- {row['id']} ({row.get('title') or 'untitled'}): "
                                      f"[{alert['severity']}] {alert['message']} "
                                      f"{alert.get('advice', '')}".rstrip())
    except Exception:
        return None

    if not urgent:
        return ("Nothing on any of the user's cases needs them right now. Say "
                "exactly that — do not manufacture a task to seem useful.")
    return ("Everything currently waiting on the user, across all their open "
            "cases. Answer ONLY from this list:\n\n" + "\n".join(urgent))


def _worth_context(query: str, workspace: str) -> str | None:
    """What cases like this have actually recovered, from closed-case history.

    Deliberately structural and never per-person: `outcomes` stores the shape of
    a resolution, not its contents. When there is no history the answer must say
    there is none — a made-up "typically £200" is the single most harmful thing
    this mode could produce, because people decide whether to bother on it.
    """
    try:
        from agentx import outcomes, store as axstore
    except Exception:
        return None
    try:
        axstore.ensure_schema()
        with axstore.connect() as conn:
            rows = outcomes.history(conn, workspace=workspace, counterparty=None,
                                    problem_type=None)
    except Exception:
        return None

    resolved = [r for r in rows if r.get("outcome") == "resolved"]
    if len(resolved) < 3:
        return ("Agent X has resolved too few cases to say what claims like this "
                "typically recover. Say that plainly — do not estimate a figure.")

    ratios = [r["recovery_ratio"] for r in resolved if r.get("recovery_ratio") is not None]
    escalated = sum(1 for r in resolved if r.get("escalated"))
    chases = [r["chases_needed"] for r in resolved if r.get("chases_needed") is not None]
    lines = [f"Closed cases on record: {len(rows)} ({len(resolved)} resolved)."]
    if ratios:
        lines.append(f"Median share of the claim recovered: "
                     f"{sorted(ratios)[len(ratios) // 2]:.0%}.")
    if chases:
        lines.append(f"Median follow-ups needed before resolution: "
                     f"{sorted(chases)[len(chases) // 2]}.")
    lines.append(f"Needed escalation beyond the first refusal: "
                 f"{escalated} of {len(resolved)}.")
    lines.append("These are structural counts from Agent X's own closed cases, "
                 "not an industry statistic and not a prediction for this user. "
                 "Present them as such.")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# the catalogue
# ─────────────────────────────────────────────────────────────────────────────
_MODES: tuple[Mode, ...] = (
    Mode("research", "Research", GENERAL, deep_retrieval=True,
         prompt=" Focus on thorough investigation, citing distinct evidence, and "
                "comprehensive context synthesis."),
    Mode("create", "Create", GENERAL, temperature=0.7,
         prompt=" Focus on creative drafting, generating fresh ideas, and engaging prose."),
    Mode("analyze", "Analyze", GENERAL,
         prompt=" Focus on structured analysis, key metric extraction, and identifying patterns."),
    Mode("build", "Build", GENERAL,
         prompt=" Focus on technical accuracy, actionable code/system steps, and "
                "implementation clarity."),
    Mode("decide", "Decide", GENERAL,
         prompt=" Focus on evaluating trade-offs, comparing options objectively, and "
                "structured decision making."),
    Mode("learn", "Learn", GENERAL,
         prompt=" Focus on step-by-step explanations, clear analogies, and educational clarity."),

    # ── consumer goals ────────────────────────────────────────────────────
    Mode("verify_booking", "Verify Booking", CONSUMER,
         prompt=" The user wants a booking or order checked. Work only from the "
                "stored record: confirm what was actually booked, the amount, the "
                "dates and the reference, and name anything the record does not "
                "establish rather than assuming it. Do not reassure — report."),
    Mode("hidden_fees", "Hidden Fees", CONSUMER,
         prompt=" The user suspects charges they did not expect. Itemise every "
                "amount on record, separate what was clearly disclosed from what "
                "was not, and state plainly which charges the record cannot "
                "account for. Never describe a fee as legitimate merely because a "
                "merchant listed it."),
    Mode("refund_policy", "Refund Policy", CONSUMER,
         prompt=" The user wants to know what they can get back. Distinguish "
                "clearly between what a merchant's own terms offer and what a "
                "statutory right guarantees, and say which applies here. Where the "
                "record does not establish the governing terms, say that instead "
                "of describing a typical policy."),
    Mode("report_issue", "Report Issue", CONSUMER,
         prompt=" Help the user report a problem effectively. Produce what a "
                "complaints team needs: what happened, when, the amounts and "
                "references on record, and what is being asked for. Flag anything "
                "the user would need to supply that the record does not have."),
    Mode("dispute_letter", "Dispute Letter", CONSUMER,
         prompt=" Draft a dispute letter using only figures, dates and references "
                "that appear in the record. State the remedy sought and a deadline "
                "for reply. Do not cite a statute unless it appears in the context "
                "above. For a letter that is signed, chased and verified, open a "
                "case instead — this is a draft, not a sent action."),
    Mode("escalation_route", "Escalation Route", CONSUMER, context=_escalation_context,
         prompt=" The user wants to know who to complain to next. Give the ladder "
                "in order — the counterparty's own process first, then the "
                "ombudsman or regulator — using ONLY the retrieved guidance above, "
                "and name the source for each step. If the guidance does not cover "
                "this sector, say so and do not name a regulator from memory."),
    Mode("case_status", "Where Am I", CONSUMER, context=_case_status_context,
         prompt=" The user wants to know where their cases stand. For each open "
                "case give the stage, who it is waiting on, and the one thing "
                "that would move it — using ONLY the case summaries above. Do not "
                "describe a case that is not listed, and if there are none, say "
                "so and offer to open one."),
    Mode("needs_attention", "Needs You", CONSUMER, context=_attention_context,
         prompt=" List only what genuinely needs the user right now, most urgent "
                "first, naming the case each item belongs to. Use ONLY the list "
                "above. If it says nothing needs them, say exactly that — do not "
                "invent a task to seem useful."),
    Mode("what_its_worth", "What It's Worth", CONSUMER, context=_worth_context,
         prompt=" The user wants to know whether pursuing this is worth it. Use "
                "ONLY the closed-case figures above, present them as Agent X's own "
                "history rather than an industry benchmark, and give no figure at "
                "all if none is provided. Say what typically has to happen — how "
                "many follow-ups, whether escalation was needed."),
)

CATALOGUE: dict[str, Mode] = {m.id: m for m in _MODES}


def get(mode_id: str | None) -> Mode | None:
    return CATALOGUE.get((mode_id or "").strip()) if mode_id else None


def catalogue() -> dict:
    """Every selectable track, grouped — what the picker is built from.

    The third group is not a goal but an INPUT track: dictation is a way of
    asking, not a way of answering, so it carries no prompt and never reaches
    `ask()`. It is listed here because the picker is where a user looks to find
    out what Agent X can do, and "you can speak instead of typing" belongs in
    that answer rather than being discoverable only by noticing an icon.
    `available` is a fact about the server; the browser feature-detects the rest.
    """
    from agentx import speech

    voice = speech.availability()
    return {
        "groups": [
            {"id": GENERAL, "label": "General",
             "modes": [m.as_dict() for m in _MODES if m.group == GENERAL]},
            {"id": CONSUMER, "label": "Consumer",
             "modes": [m.as_dict() for m in _MODES if m.group == CONSUMER]},
            {"id": INPUT, "label": "Input", "modes": [{
                "id": "voice",
                "label": "Speak instead of typing",
                "group": INPUT,
                "grounded": False,
                "action": "dictate",
                "available": voice["voice_intake"],
                "detail": voice["device_recognition"]["detail"],
                "audio_retained": voice["audio_retained"],
            }]},
        ],
        "default": "auto",
    }
