"""
Capability tracks — the one place that knows what Agent X can do.

Agent X grew from one capability (verifiable erasure) to several, and is now
absorbing more from separate codebases. Without a single registry that becomes
ten applications sharing a repository, which is the exact outcome this file
exists to prevent: one product, many tracks, one place they are declared.

WHAT A TRACK IS

A track is a capability a person can name — "understand this contract", "get my
money back", "recover a service that went down". It is deliberately NOT a
module, a service, or a source project. The internal engineering behind a track
may be a Python package inside `agentx/`, a vendored subsystem, or a separate
process; the user should not be able to tell, and the name they see never
mentions the project it came from.

STATUS IS HONEST, ALWAYS

The hardest requirement here is that this file must not lie. A registry is the
first thing a person reads to decide what to try, so a track claiming to be
available when its backend is absent is worse than one that says it is not
wired: the first wastes their afternoon, the second takes a second to read.

    live          implemented in this process, backed by real code, usable now
    service       real implementation exists but runs as a separate process that
                  is not currently reachable
    unavailable   declared, not yet connected — named so it is discoverable and
                  so its absence is visible rather than quietly missing

`status()` resolves this at call time by importing the backing module or probing
the service. Nothing is hardcoded to "live", because a hardcoded status is a
claim that survives the code it describes being deleted.

PERMISSION IS THE GOVERNOR'S, NOT A TRACK'S

Each track declares the highest-risk thing it can do, and that flows into the
existing `governor.assess()` rather than into a second approval system. A track
cannot grant itself authority by declaring a low risk: the governor reads the
action verb and the case's autonomy level, exactly as it does today.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field

# What a track is allowed to do at its most consequential, mapped onto the
# existing autonomy vocabulary rather than a new one.
#   think     reason and explain only; touches nothing
#   prepare   produce a draft or plan, execute nothing
#   act       may perform reversible actions on the user's own data
#   approve   may request authorisation for a consequential external action
THINK, PREPARE, ACT, APPROVE = "think", "prepare", "act", "approve"

LIVE, SERVICE, UNAVAILABLE = "live", "service", "unavailable"


@dataclass(frozen=True)
class Track:
    """One capability, as a person would describe it."""
    id: str
    name: str
    summary: str                       # one line, no jargon, no project names
    examples: tuple[str, ...]
    autonomy: str = PREPARE
    # The module that backs this track. Imported to decide `live`; absent module
    # means the track is honestly reported as not wired.
    module: str | None = None
    # For tracks whose real implementation runs as its own process.
    service_env: str | None = None
    # Where a user goes to use it. None means it is reachable through the
    # assistant rather than having a screen of its own.
    route: str | None = None
    # The codebase this capability's engineering came from. Internal only — it
    # is never shown to a user, and exists so a maintainer can find the source.
    origin: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    def status(self) -> str:
        """Resolved at call time. Never a stored claim.

        A module that IMPORTS is not a capability that WORKS, and conflating the
        two is the exact dishonesty this registry exists to avoid: a vendored
        subsystem whose training stack or upstream service is absent imports
        perfectly and can do nothing. So when a subsystem publishes `available()`
        — a dict saying whether it can actually run — that answer wins, and the
        bare import is only the fallback for modules that publish no such thing.
        """
        if self.module:
            try:
                module = importlib.import_module(self.module)
            except Exception:
                return UNAVAILABLE
            check = getattr(module, "available", None)
            if callable(check):
                try:
                    state = check()
                except Exception:
                    return UNAVAILABLE
                if not isinstance(state, dict):
                    return UNAVAILABLE
                if state.get("available"):
                    return LIVE
                # It is real code that is simply not connected here. When it is
                # waiting on its own process, say so rather than calling it
                # missing — the difference tells a maintainer what to do next.
                return SERVICE if state.get("endpoint_configured") is False \
                    else UNAVAILABLE
            return LIVE
        if self.service_env:
            import os
            return SERVICE if os.environ.get(self.service_env) else UNAVAILABLE
        return UNAVAILABLE

    def detail(self) -> str | None:
        """The subsystem's own account of why it is or is not usable."""
        if not self.module:
            return None
        try:
            module = importlib.import_module(self.module)
            check = getattr(module, "available", None)
            if not callable(check):
                return None
            state = check()
            return state.get("detail") if isinstance(state, dict) else None
        except Exception:
            return None

    def as_dict(self) -> dict:
        state = self.status()
        return {
            "id": self.id,
            "name": self.name,
            "summary": self.summary,
            "examples": list(self.examples),
            "autonomy": self.autonomy,
            "status": state,
            "usable": state == LIVE,
            "route": self.route,
            "tags": list(self.tags),
            # Why it is or is not usable, in the subsystem's own words.
            "detail": self.detail(),
            # Deliberately included for maintainers and deliberately not
            # surfaced by the UI — see the module docstring.
            "origin": self.origin,
        }


_TRACKS: tuple[Track, ...] = (
    # ── shipped and load-bearing today ────────────────────────────────────
    Track(
        id="consumer_resolution",
        name="Consumer Resolution",
        summary="Describe what went wrong and Agent X works out what you are "
                "owed, drafts the request, chases it, and proves what happened.",
        examples=("I was charged twice for the same order",
                  "My flight was cancelled and they refuse to pay",
                  "This subscription renewed without telling me"),
        autonomy=APPROVE, module="agentx.engine", route="/agentx",
        origin="agentx (native); consumer-justice concepts",
        tags=("disputes", "money", "letters")),
    Track(
        id="documents_evidence",
        name="Documents and Evidence",
        summary="Read the documents behind a problem, pull out the facts that "
                "matter, and say plainly what could not be read.",
        examples=("Read this PDF invoice and tell me what I was charged",
                  "Does this document actually relate to my case?"),
        autonomy=THINK, module="agentx.documents", route="/agentx",
        origin="agentx (native)",
        tags=("documents", "ocr", "evidence")),
    Track(
        id="knowledge_research",
        name="Regulatory Research",
        summary="Find the published rule that applies, cite it, and check the "
                "citation actually says what it is claimed to say.",
        examples=("Who do I complain to about my bank?",
                  "What are my rights when a flight is cancelled?"),
        autonomy=THINK, module="agentx.knowledge", route="/agentx",
        origin="agentx (native); consumer-justice regulatory corpus",
        tags=("research", "citations", "regulation")),
    Track(
        id="verified_erasure",
        name="Verified Erasure",
        summary="Erase your data completely and hand you proof it is gone that "
                "you can check without trusting us.",
        examples=("Delete everything you hold about me",
                  "Prove this record was really destroyed"),
        autonomy=APPROVE, module="core.forget", route="/app",
        origin="agentx (native)",
        tags=("privacy", "erasure", "proof")),
    Track(
        id="voice_intake",
        name="Voice",
        summary="Say what happened instead of typing it. The recording never "
                "leaves your device and is never stored.",
        examples=("Describe a dispute out loud",
                  "Dictate a question to the assistant"),
        autonomy=THINK, module="agentx.speech", route="/agentx",
        origin="voicegraph (voice intake)",
        tags=("voice", "accessibility")),
    Track(
        id="system_recovery",
        name="Autonomous Recovery",
        summary="Agent X watches its own work, finds what has stopped moving, "
                "and fixes what it is permitted to fix.",
        examples=("Is anything of mine stuck?",
                  "Why has this case not moved in two weeks?"),
        autonomy=ACT, module="agentx.sentinel", route="/agentx",
        origin="sre-sentinel (detect/remediate/verify loop)",
        tags=("reliability", "self-healing")),
    Track(
        id="agent_activity",
        name="Activity and Audit",
        summary="Every step Agent X took, in order, signed, and checkable by "
                "someone who does not trust Agent X.",
        examples=("Show me exactly what happened on my case",
                  "Verify this receipt is genuine"),
        autonomy=THINK, module="agentx.chain", route="/verify",
        origin="agentx (native)",
        tags=("audit", "transparency")),
    Track(
        id="vulnerability_remediation",
        name="Vulnerability Remediation",
        summary="Find a vulnerable dependency, work out whether it can "
                "actually be reached, and prepare the upgrade that fixes it.",
        examples=("Is this vulnerable package actually exploitable here?",
                  "Prepare the upgrade that fixes this advisory"),
        autonomy=APPROVE, module="agentx.subsystems.sentinel_x",
        route="/api/agentx/sentinel_x/status",
        origin="SENTINEL (AppSec triage fleet), vendored whole",
        tags=("security", "appsec", "remediation")),

    # ── declared, not yet connected ───────────────────────────────────────
    # Named here on purpose. A capability that exists in a source repository but
    # is not wired is a fact about this product, and hiding it until it works
    # would make this registry a wish list rather than a description.
    Track(
        id="contract_intelligence",
        name="Contract Intelligence",
        summary="Turn a contract into something you can act on — where the risk "
                "sits, what you owe, and by when.",
        examples=("Show me the risky clauses in this agreement",
                  "When does this renew, and what does it cost me to leave?"),
        autonomy=THINK, module="agentx.subsystems.contracts",
        origin="dealdesk (Next.js generative-UI app), vendored + service bridge",
        tags=("contracts", "risk", "obligations")),
    Track(
        id="action_automation",
        name="Action Workflows",
        summary="Describe a multi-step job and Agent X turns it into a workflow "
                "you can watch run, step by step.",
        examples=("Summarise my notes and email them to me",
                  "Collect these records and file them"),
        autonomy=APPROVE, service_env="AGENT_X_WORKFLOW_URL",
        origin="voicegraph (workflow execution engine)",
        tags=("automation", "workflows")),
    Track(
        id="safe_operations",
        name="Safe Operations",
        summary="Detect an operational problem, prepare the fix, and hold it "
                "until a person approves anything risky.",
        examples=("Something is failing — what would you do about it?",
                  "Prepare the fix but do not apply it yet"),
        autonomy=APPROVE, module="agentx.subsystems.safe_ops",
        origin="opsguard (Motia approval workflow), vendored + service bridge",
        tags=("operations", "approval")),
    Track(
        id="infrastructure_intelligence",
        name="Infrastructure Intelligence",
        summary="Look at how an application is deployed and find ways to make "
                "it cheaper, faster, or more reliable.",
        examples=("What is wrong with how this project is deployed?",
                  "How would I cut the running cost of this service?"),
        autonomy=PREPARE, module="agentx.subsystems.infrastructure",
        route="/api/agentx/infrastructure/status",
        origin="infoundry (architecture brain), vendored",
        tags=("infrastructure", "cost", "reliability")),
    Track(
        id="model_studio",
        name="Model Studio",
        summary="Build a small model from your own data — prepare it, train it, "
                "and see honestly how well it performs.",
        examples=("Train a model on this dataset",
                  "How good is the model I just built?"),
        autonomy=PREPARE, module="agentx.subsystems.model_studio",
        origin="vriksha-ai (Oumi training pipeline), vendored",
        tags=("training", "models", "evaluation")),
    Track(
        id="learning_memory",
        name="Learning Memory",
        summary="Remember what someone has learned, find the gaps, and suggest "
                "what to work on next.",
        examples=("What am I still weak on?",
                  "Make me a quiz on what I keep getting wrong"),
        autonomy=THINK, module="agentx.subsystems.learning",
        route="/api/agentx/learning/status",
        origin="classroom-memory (learner state + quiz generation), vendored",
        tags=("learning", "memory", "quizzes")),
    Track(
        id="emergency_response",
        name="Emergency Response",
        summary="Take an emergency report, work out how urgent it is and where "
                "it is, and get it to the right responders.",
        examples=("Report a flood at this address",
                  "Who needs to be told about this incident?"),
        autonomy=APPROVE, module="agentx.subsystems.emergency",
        origin="dcrca (Portia triage + dispatch plan), vendored",
        tags=("emergency", "dispatch", "urgent")),
    Track(
        id="agent_observability",
        name="Agent Observability",
        summary="See what Agent X's own agents did — which tools ran, how long "
                "they took, what they cost, and where they failed.",
        examples=("Why was that slow?",
                  "What did this agent actually call?"),
        autonomy=THINK, module="agentx.subsystems.observability",
        route="/api/agentx/observability/status",
        origin="opentelemetry-instrumentation-crewai, vendored",
        tags=("observability", "telemetry", "admin")),
    Track(
        id="agriculture",
        name="Field Intelligence",
        summary="Answer a grower's question with the stage their crop is at, "
                "what the rules allow, and what the prices are doing.",
        examples=("My claim was rejected — can I appeal?",
                  "What support am I eligible for?"),
        autonomy=THINK, service_env="AGENT_X_FIELD_URL",
        origin="agri-bot (phase-aware guidance, scheme eligibility)",
        tags=("agriculture", "eligibility")),
)

CATALOGUE: dict[str, Track] = {t.id: t for t in _TRACKS}


def get(track_id: str | None) -> Track | None:
    return CATALOGUE.get((track_id or "").strip()) if track_id else None


def catalogue(*, usable_only: bool = False) -> list[dict]:
    """Every track, resolved. Usable ones first, then the rest, each stable."""
    rows = [t.as_dict() for t in _TRACKS]
    if usable_only:
        rows = [r for r in rows if r["usable"]]
    rows.sort(key=lambda r: (not r["usable"], r["name"]))
    return rows


def summary() -> dict:
    """What Agent X can do right now, counted honestly."""
    rows = catalogue()
    by_status: dict[str, int] = {}
    for row in rows:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    return {
        "tracks": len(rows),
        "usable_now": sum(1 for r in rows if r["usable"]),
        "by_status": by_status,
        "note": ("A track is reported usable only if the code behind it imports "
                 "in this process. Tracks backed by a separate service are "
                 "listed as such rather than as available."),
    }
