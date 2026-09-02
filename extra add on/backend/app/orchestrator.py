"""Selectable orchestration layer.

The same six real agent functions (app/agent_tools.py) can be driven three
different ways, chosen with SENTINEL_ORCHESTRATOR:

  direct  (default) - worker.py calls the tool functions in sequence itself.
                      Deterministic, no orchestration LLM, cheapest to run.
  adk               - Google Agent Development Kit SequentialAgent
                      (app/adk_app/agent.py) drives the same tools.
  strands           - AWS Strands Agents SDK Agent
                      (app/strands_app/agent.py) drives the same tools.

All three execute identical underlying logic - the same npm audit, the same
Gemini calls, the same git-worktree sandbox, the same signing - because they
all bottom out in agent_tools.py. Only *who decides the call order* differs:
`direct` hardcodes it, `adk` and `strands` let their SDK's model plan it.
That's the honest difference, and it's why swapping orchestrators can't
change what the evidence says.

The orchestrator is reported by GET /api/system-info so the UI can show
which one actually produced a given run rather than asserting one.
"""

from __future__ import annotations

import os

VALID_ORCHESTRATORS = ("direct", "adk", "strands")


def active_orchestrator() -> str:
    choice = os.environ.get("SENTINEL_ORCHESTRATOR", "direct").strip().lower()
    return choice if choice in VALID_ORCHESTRATORS else "direct"


def _investigation_prompt(finding_id: str) -> str:
    return (
        f"Run the full SENTINEL investigation for finding_id '{finding_id}'. "
        "Call the tools in this order, passing each result forward: "
        "1) analyst_assess_relevance, 2) patch_forge_generate_patch, "
        "3) re_verifier_confirm_fix (pass the branch_name and the patch from step 2), "
        "4) evidence_agent_seal_record (pass the verdict, the verification results, "
        "and the final patch proposal). Report the final status."
    )


class OrchestratorDidNotSeal(RuntimeError):
    """Raised when an LLM-driven orchestrator finished without producing a
    new sealed evidence record.

    This has to be a failure, not an empty success. The orchestrator is a
    model deciding which tools to call, so it can perfectly well narrate a
    complete investigation without ever calling evidence_agent_seal_record.
    Marking that job "done" would put a confident, evidence-free summary in
    front of a reviewer - the exact failure mode this whole product exists
    to prevent.
    """


def _evidence_fingerprint(finding_id: str):
    """Identity of the currently-sealed record, or None if there isn't one."""
    from app.store import get_store

    doc = get_store().get_evidence(finding_id)
    if doc is None:
        return None
    # The signature is a SHA-256 over the record's content, so a genuinely
    # new seal changes it. Falling back to the whole doc keeps this correct
    # even for records written before signing existed.
    return doc.get("signature") or doc


def _result_from_evidence(finding_id: str, name: str, transcript: list[str], before) -> dict:
    """Normalise an LLM-driven run onto the same contract the direct path
    returns.

    All three orchestrators bottom out in the same tools, so they must also
    agree on the shape of what comes back - otherwise selecting `adk` (the
    Google track's headline requirement) silently produces a result the UI
    cannot read, and a finished investigation renders as nothing at all.
    """
    from app.store import get_store

    doc = get_store().get_evidence(finding_id)
    if doc is None:
        raise OrchestratorDidNotSeal(
            f"The {name} orchestrator finished without sealing an evidence record for "
            f"{finding_id}. Nothing was written, so there is no verified result to show."
        )
    if before is not None and _evidence_fingerprint(finding_id) == before:
        raise OrchestratorDidNotSeal(
            f"The {name} orchestrator finished but the sealed record for {finding_id} is "
            "unchanged from before this run - it did not seal anything new, and the "
            "existing record belongs to an earlier investigation."
        )

    patch = doc.get("patch_proposal")
    return {
        "orchestrator": name,
        # Kept so a reviewer can see how the model chose to drive the tools;
        # the verified data below comes from the sealed record, never from
        # the model's narration of it.
        "transcript": transcript,
        "verdict": doc.get("verdict"),
        "patch": patch,
        "reverify": {
            "results": doc.get("verification_results") or [],
            "final_patch_proposal": patch,
        },
        "evidence": doc,
    }


# The orchestration SDKs make their own Gemini calls, outside app/llm.py, so
# app.llm's retry policy does not cover them. A real ADK run died twice on
# "503 This model is currently experiencing high demand" - once after 324
# seconds of genuine work. The model being briefly oversubscribed is not a
# failed investigation, and must not be reported as one.
_TRANSIENT_MARKERS = ("503", "UNAVAILABLE", "high demand", "overloaded", "502", "504", "429")


def _is_transient(exc: BaseException) -> bool:
    """True for upstream capacity errors, which are worth another attempt.

    Matched on the message rather than the exception class because ADK and
    Strands surface these through different SDK-specific types, and pinning
    the class names would silently stop working on an SDK upgrade.
    """
    text = f"{type(exc).__name__}: {exc}"
    return any(m in text for m in _TRANSIENT_MARKERS)


def _with_retry(label: str, run, attempts: int = 3):
    """Retry an orchestration run through transient upstream failures."""
    import time

    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            return run()
        except OrchestratorDidNotSeal:
            # A completed run that sealed nothing is a real outcome, not a
            # capacity blip - retrying would just repeat it.
            raise
        except Exception as exc:  # noqa: BLE001
            if not _is_transient(exc) or attempt == attempts - 1:
                raise
            last = exc
            wait = min(2**attempt * 10, 45)
            print(f"[{label}] transient upstream error, retrying in {wait}s: {str(exc)[:120]}")
            time.sleep(wait)
    raise last  # unreachable, kept for type-checkers


def run_via_adk(finding_id: str) -> dict:
    """Drives the real Google ADK SequentialAgent over the same tools."""
    import asyncio

    from google.adk.runners import InMemoryRunner
    from google.genai import types

    from app.adk_app.agent import root_agent

    async def _run() -> list[str]:
        runner = InMemoryRunner(agent=root_agent, app_name="sentinel")
        session = await runner.session_service.create_session(app_name="sentinel", user_id="worker")
        message = types.Content(role="user", parts=[types.Part(text=_investigation_prompt(finding_id))])
        transcript: list[str] = []
        async for event in runner.run_async(user_id="worker", session_id=session.id, new_message=message):
            content = getattr(event, "content", None)
            for part in getattr(content, "parts", None) or []:
                if getattr(part, "text", None):
                    transcript.append(part.text)
        return transcript

    before = _evidence_fingerprint(finding_id)
    transcript = _with_retry("adk", lambda: asyncio.run(_run()))
    return _result_from_evidence(finding_id, "adk", transcript, before)


def run_via_strands(finding_id: str) -> dict:
    """Drives the real AWS Strands Agent over the same tools."""
    from app.strands_app.agent import build_agent

    before = _evidence_fingerprint(finding_id)
    agent = build_agent()
    result = _with_retry("strands", lambda: agent(_investigation_prompt(finding_id)))
    return _result_from_evidence(finding_id, "strands", [str(result)], before)
