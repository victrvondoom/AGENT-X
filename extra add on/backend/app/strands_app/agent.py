"""SENTINEL's AWS Strands Agents SDK orchestration layer - the "Professional
Agents" track submission for Agents for Humans.

A single real Strands `Agent`, built with the Strands Agents SDK (not a
relabeled copy of the Google build), given the same six real tools that
wrap the actual core engine (app/agent_tools.py -> app/agents/*.py). It
targets the same repetitive, judgment-heavy professional work the track
calls for: investigating a dependency finding, verifying it, patching it,
and proving the patch worked.

Uses Strands' built-in Gemini model provider so this runs today with only
the existing Gemini API key - no AWS account needed to exercise the agent
itself. Swapping `model=` to `BedrockModel(...)` is a one-line change once
AWS/Bedrock access is available; the tools and orchestration are identical
either way, which is the whole point of building the core engine once.
"""

from __future__ import annotations

from strands import Agent
from strands.models.gemini import GeminiModel

from app.config import GEMINI_API_KEY, GEMINI_MODEL
from app.strands_app.tools import (
    analyst_assess_relevance,
    evidence_agent_seal_record,
    hunter_scan,
    patch_forge_generate_patch,
    re_verifier_confirm_fix,
    verification_lab_run_scenario,
)

SYSTEM_PROMPT = """You are SENTINEL, an autonomous security-verification agent for professional \
security engineers. You investigate a dependency-vulnerability finding end to end, following this \
exact six-stage loop, calling one tool per stage in order:

1. hunter_scan - discover real findings via a real npm audit scan.
2. analyst_assess_relevance - decide if the finding is actually reachable in this codebase.
3. verification_lab_run_scenario (branch="master") - dynamically prove exploitability in a real sandbox.
4. patch_forge_generate_patch - generate a real, minimal fix and commit it to a real git branch.
5. re_verifier_confirm_fix - re-run the real scenario against the fix branch, correcting it if needed.
6. evidence_agent_seal_record - assemble and cryptographically sign the final evidence record.

Never claim a result a tool did not actually return. Never skip a stage. Report each tool's real \
output (branch names, sandbox IDs, signatures, verdicts) verbatim rather than paraphrasing away the \
concrete evidence - the entire point of this system is that every claim is backed by a real, \
inspectable artifact, not an LLM's opinion."""


def build_agent() -> Agent:
    model = GeminiModel(
        client_args={"api_key": GEMINI_API_KEY},
        model_id=GEMINI_MODEL,
    )
    return Agent(
        name="sentinel_fleet",
        description="Autonomous security-finding investigation, verification, and remediation agent.",
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=[
            hunter_scan,
            analyst_assess_relevance,
            verification_lab_run_scenario,
            patch_forge_generate_patch,
            re_verifier_confirm_fix,
            evidence_agent_seal_record,
        ],
    )


if __name__ == "__main__":
    agent = build_agent()
    result = agent(
        "Investigate the jsonwebtoken finding (SENTINEL-F-GHSA-8cf7-32gw-wr33) end to end: "
        "scan for it, assess relevance, verify exploitability on master, generate a patch, "
        "re-verify the fix, and seal the evidence record."
    )
    print(result)
