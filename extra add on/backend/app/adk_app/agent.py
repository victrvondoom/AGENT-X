"""SENTINEL's Google ADK orchestration layer.

Wraps the same six real agents (Hunter, Analyst, Verification Lab,
Patch Forge, Re-Verifier, Evidence Agent) as a real `SequentialAgent`
workflow of ADK `LlmAgent`s, each backed by Gemini. This is the literal
"Google Agent Development Kit" + "Gemini via the Gemini API" requirement
for the Fortified Enterprise Fleet track - runnable right now, locally,
with only a Gemini API key (no GCP project/billing needed to exercise the
orchestration itself; Cloud Run/Firestore/Pub/Sub are the deployment target
once real GCP credentials are available - see backend/app/gcp/ adapters).

Run it locally:
    cd backend
    ./.venv/Scripts/python.exe -m google.adk.cli web app.adk_app
  or, non-interactively:
    ./.venv/Scripts/python.exe -m app.adk_app.agent
"""

from __future__ import annotations

import os

from app.config import GEMINI_API_KEY

# ADK talks to Gemini either via Vertex AI or directly via the Gemini API
# (AI Studio backend) depending on this flag. Forcing the direct API path
# is what makes the orchestration runnable with only a Gemini key, no GCP
# project - the same key already used by app/llm.py.
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")
if GEMINI_API_KEY:
    os.environ.setdefault("GOOGLE_API_KEY", GEMINI_API_KEY)

from google.adk.agents import LlmAgent, SequentialAgent  # noqa: E402
from google.adk.tools import FunctionTool  # noqa: E402

from app.adk_app.tools import (  # noqa: E402
    analyst_assess_relevance,
    evidence_agent_seal_record,
    hunter_scan,
    patch_forge_generate_patch,
    re_verifier_confirm_fix,
    verification_lab_run_scenario,
)

from app.config import GEMINI_MODEL

MODEL = GEMINI_MODEL

hunter_agent = LlmAgent(
    name="hunter",
    model=MODEL,
    description="Discovers real dependency-vulnerability findings via npm audit.",
    instruction=(
        "You are Hunter, a security-finding discovery agent. Call hunter_scan to run a real "
        "npm audit scan of the demo repository and report the findings you discovered, "
        "referencing their real advisory IDs and severities. Never invent a finding that "
        "hunter_scan did not return."
    ),
    tools=[FunctionTool(hunter_scan)],
    output_key="hunter_output",
)

analyst_agent = LlmAgent(
    name="analyst",
    model=MODEL,
    description="Assesses whether a finding is actually reachable/relevant in this codebase.",
    instruction=(
        "You are Analyst. Given a finding_id from the prior Hunter output, call "
        "analyst_assess_relevance to get a real reachability-grounded verdict, then report "
        "the verdict and reasoning verbatim - do not soften or restate the reasoning in a way "
        "that loses the concrete file/line evidence it cites."
    ),
    tools=[FunctionTool(analyst_assess_relevance)],
    output_key="analyst_output",
)

verification_lab_agent = LlmAgent(
    name="verification_lab",
    model=MODEL,
    description="Dynamically proves or disproves exploitability in a real sandbox.",
    instruction=(
        "You are Verification Lab. Call verification_lab_run_scenario with branch='master' to "
        "test the vulnerable baseline. Report the real result (CONFIRMED_EXPLOITABLE or "
        "RESOLVED), the sandbox_id, and duration_ms exactly as returned."
    ),
    tools=[FunctionTool(verification_lab_run_scenario)],
    output_key="verification_output",
)

patch_forge_agent = LlmAgent(
    name="patch_forge",
    model=MODEL,
    description="Generates a real, minimal remediation and commits it to a real git branch.",
    instruction=(
        "You are Patch Forge. Call patch_forge_generate_patch for the finding under "
        "investigation. Report the real branch name, files changed, and explanation exactly "
        "as returned - never describe a file as changed unless it appears in files_changed."
    ),
    tools=[FunctionTool(patch_forge_generate_patch)],
    output_key="patch_output",
)

re_verifier_agent = LlmAgent(
    name="re_verifier",
    model=MODEL,
    description="Re-runs the real scenario against the fix branch, correcting if it still fails.",
    instruction=(
        "You are Re-Verifier. Call re_verifier_confirm_fix with the finding_id and the branch "
        "name Patch Forge produced. Report whether the finding is genuinely RESOLVED, including "
        "any corrective iteration that was required - do not claim resolution unless the tool's "
        "own results say RESOLVED."
    ),
    tools=[FunctionTool(re_verifier_confirm_fix)],
    output_key="reverify_output",
)

evidence_agent = LlmAgent(
    name="evidence_agent",
    model=MODEL,
    description="Assembles and signs the final evidence record for the finding.",
    instruction=(
        "You are Evidence Agent. Call evidence_agent_seal_record for the finding under "
        "investigation and report the final_status and signature exactly as returned."
    ),
    tools=[FunctionTool(evidence_agent_seal_record)],
    output_key="evidence_output",
)

root_agent = SequentialAgent(
    name="sentinel_fleet",
    description=(
        "SENTINEL's six-stage autonomous verification loop: Hunter -> Analyst -> "
        "Verification Lab -> Patch Forge -> Re-Verifier -> Evidence Agent, each a real "
        "tool-calling ADK LlmAgent backed by Gemini."
    ),
    sub_agents=[
        hunter_agent,
        analyst_agent,
        verification_lab_agent,
        patch_forge_agent,
        re_verifier_agent,
        evidence_agent,
    ],
)


if __name__ == "__main__":
    import asyncio

    from google.adk.runners import InMemoryRunner
    from google.genai import types

    async def _main() -> None:
        runner = InMemoryRunner(agent=root_agent, app_name="sentinel")
        session = await runner.session_service.create_session(app_name="sentinel", user_id="demo")
        prompt = (
            "Investigate the jsonwebtoken finding (SENTINEL-F-GHSA-8cf7-32gw-wr33) end to end: "
            "scan for it, assess relevance, verify exploitability on master, generate a patch, "
            "re-verify the fix, and seal the evidence record."
        )
        async for event in runner.run_async(
            user_id="demo",
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text=prompt)]),
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        print(f"[{event.author}] {part.text}\n")

    asyncio.run(_main())
