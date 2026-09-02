"""Framework-agnostic tool functions wrapping SENTINEL's actual core-engine
agents (hunter.py, analyst.py, verification_lab.py, patch_forge.py,
re_verifier.py, evidence_agent.py). No re-implementation, no mocked
behavior - these are the exact same functions already verified against the
real Juice Shop clone. Both app/adk_app/tools.py (Google ADK) and
app/strands_app/tools.py (AWS Strands) wrap these same plain functions with
their respective SDK's tool decorator/class, so both orchestration layers
call identical real logic instead of two drifting reimplementations.

Every tool takes and returns plain JSON-serializable types (str/dict/list)
because both ADK and Strands build the LLM-facing tool schema from the
function signature and docstring.
"""

from __future__ import annotations

from pathlib import Path

from agentx.subsystems.sentinel_x.agents.analyst import analyze
from agentx.subsystems.sentinel_x.agents.evidence_agent import assemble_evidence
from agentx.subsystems.sentinel_x.agents.hunter import hunt
from agentx.subsystems.sentinel_x.agents.patch_forge import generate_patch
from agentx.subsystems.sentinel_x.agents.re_verifier import reverify
from agentx.subsystems.sentinel_x.agents.verification_lab import run_scenario
from agentx.subsystems.sentinel_x.config import DEMO_REPO_DIR, GEMINI_MODEL
from agentx.subsystems.sentinel_x.governance.gateway import enforce
from agentx.subsystems.sentinel_x.observability import traced_agent
from agentx.subsystems.sentinel_x.store import get_store
from agentx.subsystems.sentinel_x.schemas import (
    Finding,
    PatchProposal,
    RelevanceVerdict,
    VerificationResult,
)

_REPO_DIR = DEMO_REPO_DIR


def _find_finding(finding_id: str) -> Finding:
    findings = hunt(_REPO_DIR)
    match = next((f for f in findings if f.finding_id == finding_id), None)
    if match is None:
        raise ValueError(f"No such finding: {finding_id}")
    return match


@traced_agent("hunter")
@enforce("hunter", "run_npm_audit")
def hunter_scan(component_filter: str = "") -> dict:
    """Runs a real `npm audit` scan of the demo repository and returns every
    real finding it detects. Optionally filter to findings whose component
    name contains `component_filter` (case-insensitive substring match)."""
    findings = hunt(_REPO_DIR)
    if component_filter:
        findings = [f for f in findings if component_filter.lower() in f.component.lower()]
    return {"count": len(findings), "findings": [f.model_dump(mode="json") for f in findings]}


@traced_agent("analyst", model=GEMINI_MODEL)
@enforce("analyst", "call_llm")
def analyst_assess_relevance(finding_id: str) -> dict:
    """Runs the real Analyst agent: a regex-based reachability scan of the
    application's own source plus one genuine Gemini call, to decide whether
    `finding_id` is actually reachable/relevant in this codebase."""
    finding = _find_finding(finding_id)
    verdict = analyze(finding, _REPO_DIR)
    return verdict.model_dump(mode="json")


@traced_agent("verifier")
@enforce("verifier", "execute_sandbox")
def verification_lab_run_scenario(finding_id: str, branch: str) -> dict:
    """Runs the real Verification Lab sandbox scenario for `finding_id`
    against `branch` (e.g. "master" for the vulnerable baseline, or a
    Patch Forge fix branch) - a genuine isolated git-worktree sandbox that
    installs the real pinned dependency version and attempts a real
    algorithm-confusion forgery against it."""
    finding = _find_finding(finding_id)
    result = run_scenario(finding, _REPO_DIR, branch)
    return result.model_dump(mode="json")


@traced_agent("patch-forge", model=GEMINI_MODEL)
@enforce("patch-forge", "call_llm")
@enforce("patch-forge", "create_branch")
def patch_forge_generate_patch(finding_id: str, verification_feedback: str = "") -> dict:
    """Runs the real Patch Forge agent: one genuine Gemini call producing
    minimal exact-substring code replacements (validated and applied via
    Python's own str.replace, never trusting the model's raw diff), then a
    real git branch + commit. Optionally pass `verification_feedback` (real
    prior Verification Lab evidence) to ground a corrective second attempt."""
    finding = _find_finding(finding_id)
    proposal, _ = generate_patch(
        finding, _REPO_DIR, verification_feedback=verification_feedback or None
    )
    return proposal.model_dump(mode="json")


@traced_agent("re-verifier")
@enforce("re-verifier", "trigger_patch")
def re_verifier_confirm_fix(finding_id: str, branch_name: str, patch_proposal: dict | None = None) -> dict:
    """Runs the real Re-Verifier loop: re-runs the Verification Lab scenario
    against the master baseline and the fix branch, and - if the fix branch
    is still exploitable - triggers one real corrective Patch Forge attempt
    grounded in that observed failure, then re-checks again. Returns the
    real, final PatchProposal actually confirmed (which may be a corrected
    one, not necessarily the one passed in) so downstream evidence sealing
    reflects what was actually verified, not the first attempt."""
    finding = _find_finding(finding_id)

    proposal = (
        PatchProposal(**patch_proposal)
        if patch_proposal
        else PatchProposal(
            finding_id=finding_id, branch_name=branch_name, files_changed=[], diff="",
            generated_test_paths=[], explanation="",
        )
    )
    results, final_proposal = reverify(finding, _REPO_DIR, proposal)
    return {
        "results": [r.model_dump(mode="json") for r in results],
        "final_patch_proposal": final_proposal.model_dump(mode="json"),
    }


@traced_agent("evidence-agent")
@enforce("evidence-agent", "write_evidence")
def evidence_agent_seal_record(
    finding_id: str,
    verdict: dict | None = None,
    verification_results: list[dict] | None = None,
    patch_proposal: dict | None = None,
) -> dict:
    """Runs the real Evidence Agent: reassembles the finding's full real
    timeline (Hunter, Analyst, Verification Lab, Patch Forge output already
    on disk / re-derivable) into the canonical EvidenceObject and computes a
    real SHA-256 signature over it. `verdict`, `verification_results`, and
    `patch_proposal`, when given, are the exact real dicts already returned
    by the earlier stages in this same pipeline run - passing them through
    (rather than re-deriving or dropping them) is what makes the final
    evidence timeline actually reflect what happened, instead of only ever
    containing a single "Hunter detected it" entry."""
    finding = _find_finding(finding_id)
    evidence = assemble_evidence(
        finding=finding,
        repo="juice-shop/juice-shop",
        verdict=RelevanceVerdict(**verdict) if verdict else None,
        verification_results=(
            [VerificationResult(**vr) for vr in verification_results] if verification_results else None
        ),
        patch_proposal=PatchProposal(**patch_proposal) if patch_proposal else None,
        repo_dir=_REPO_DIR,
    )
    payload = evidence.model_dump(mode="json")
    # assemble_evidence() only writes a local JSON file (EVIDENCE_DIR) - it
    # has no idea the EvidenceStore abstraction (Firestore/DynamoDB/local)
    # even exists. Every real investigation was therefore only ever landing
    # on whatever machine's disk ran the worker; the configured cloud store
    # was never actually written to by the running application, no matter
    # which orchestrator or backend was selected. Persisting here, in the
    # one function all three orchestrators share, is what makes the store
    # abstraction real instead of three unused implementations of an
    # interface nothing calls.
    get_store().put_evidence(finding_id, payload)
    return payload
