"""Re-Verifier - closes the loop on a Patch Forge fix by re-running the real
Verification Lab scenario against the fix branch. If the fix genuinely
resolves it, that's the end of the story. If Verification Lab proves the
fix branch is STILL exploitable (a real, observed outcome - this happened
during development: jsonwebtoken@0.4.0 ignores the `algorithms` verify
option entirely, so a code-only fix left it exploitable), Re-Verifier feeds
that concrete evidence back into one corrective Patch Forge attempt and
re-checks, rather than silently reporting success.

When a fix is confirmed (RESOLVED status), Re-Verifier stores it in the
memory bank's verified_fixes collection so Patch Forge can retrieve it as
a grounding pattern for similar vulnerabilities. Everything returned here
is a real VerificationResult from an actual sandboxed run - never asserted.
"""

from __future__ import annotations

from pathlib import Path

from app.agents.patch_forge import generate_patch
from app.agents.verification_lab import run_scenario
from app.memory import store_verified_fix
from app.schemas import Finding, PatchProposal, VerificationResult


def reverify(
    finding: Finding,
    repo_dir: Path,
    patch_proposal: PatchProposal,
    baseline_branch: str = "master",
    max_correction_attempts: int = 1,
) -> tuple[list[VerificationResult], PatchProposal]:
    """Returns (all VerificationResults in order, the final PatchProposal
    actually confirmed - which may be a corrected one if the first attempt
    didn't hold up)."""
    results: list[VerificationResult] = []

    baseline = run_scenario(finding, repo_dir, baseline_branch)
    results.append(baseline)

    current_proposal = patch_proposal
    for attempt in range(max_correction_attempts + 1):
        post_fix = run_scenario(finding, repo_dir, current_proposal.branch_name)
        results.append(post_fix)

        if post_fix.result == "RESOLVED":
            # Store verified fix in memory bank for future Patch Forge lookups
            cwe_id = finding.cwe[0] if finding.cwe else "UNKNOWN"
            pattern = current_proposal.diff  # Store the actual diff as the pattern
            store_verified_fix(cwe_id, "javascript", pattern, finding.finding_id)
            return results, current_proposal

        if attempt >= max_correction_attempts:
            break

        feedback = (
            f"Verification Lab re-ran the real exploit scenario against your fix on branch "
            f"'{current_proposal.branch_name}' and it STILL SUCCEEDED: {post_fix.observed}"
        )
        current_proposal, _ = generate_patch(finding, repo_dir, verification_feedback=feedback)

    return results, current_proposal


if __name__ == "__main__":
    from app.agents.analyst import analyze
    from app.agents.hunter import hunt

    repo = Path("workdir/juice-shop")
    findings = hunt(repo)
    target = next(f for f in findings if f.component == "jsonwebtoken")

    analyze(target, repo)
    proposal, _ = generate_patch(target, repo)
    print(f"Initial patch branch: {proposal.branch_name} (files: {proposal.files_changed})\n")

    results, final_proposal = reverify(target, repo, proposal)
    for r in results:
        print(f"[{r.scenario}]")
        print(f"  result: {r.result}")
        print(f"  observed: {r.observed}\n")

    print(f"Final confirmed branch: {final_proposal.branch_name} (files: {final_proposal.files_changed})")
