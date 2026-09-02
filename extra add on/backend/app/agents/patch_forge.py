"""Patch Forge - grounded fix generation using real remediation patterns.

Patch Forge MUST call retrieve_fix_pattern(cwe_id, language) first. If a
pattern exists (either from verified_fixes by Re-Verifier, or from OWASP
Cheat Sheet), use it as grounding context for code generation. If no pattern
exists, escalate to human review instead of improvising — this is the
grounding discipline applied to code, not prose.

For a dependency-CVE finding, the honest fix is almost never "edit
node_modules" - it's hardening the application's own usage and/or bumping
the dependency version. This module gives Gemini the *actual* vulnerable
usage file (found via the same reachability evidence Analyst used) and asks
for minimal, exact-substring replacements rather than a full regenerated
file. Full-file regeneration was tried first and empirically corrupted
unrelated lines (a regex literal, a duplicated token) when Gemini re-emitted
a large file as a JSON string - a real failure mode, not a hypothetical one.
Applying the model's snippets via Python's own str.replace, and rejecting
any snippet that doesn't match the original exactly once, makes untouched
code untouched by construction instead of by hoping the model behaves.
"""

from __future__ import annotations

import difflib
import json
import re
import subprocess
from pathlib import Path

from app.agents.reachability import find_reachability_evidence
from app.governance import model_armor
from app.grounded_tools import retrieve_fix_pattern
from app.llm import call_gemini
from app.schemas import Finding, PatchProposal


def _git(repo_dir: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo_dir, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _current_branch(repo_dir: Path) -> str:
    return _git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD")


_PATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "explanation": {"type": "string"},
        "replacements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "original_snippet": {"type": "string"},
                    "replacement_snippet": {"type": "string"},
                },
                "required": ["original_snippet", "replacement_snippet"],
            },
        },
        "test_file_relative_path": {"type": "string"},
        "test_file_content": {"type": "string"},
        "package_json_version_bump": {"type": "string", "nullable": True},
    },
    "required": ["explanation", "replacements", "test_file_relative_path", "test_file_content"],
}


def _build_prompt(finding: Finding, target_file: str, original_content: str, reference_pattern: str = "") -> str:
    numbered = "\n".join(
        f"{i + 1}: {line}" for i, line in enumerate(original_content.splitlines())
    )
    pattern_context = ""
    if reference_pattern:
        pattern_context = f"""
REFERENCE REMEDIATION PATTERN (from OWASP or verified prior fixes):
{reference_pattern}

Use this pattern as your grounding. Do not deviate from the pattern style."""

    return f"""You are the Patch Forge agent in SENTINEL, an autonomous security remediation fleet. \
Given a confirmed, reachable dependency vulnerability, produce a real, minimal, correct code fix in the \
application's own source - not a patch to node_modules.{pattern_context}

FINDING:
- component: {finding.component}
- advisory: {finding.advisory_id} - {finding.summary}
- severity: {finding.severity.value}, CVSS {finding.cvss_score}
- CWE: {", ".join(finding.cwe) or "unknown"}

VULNERABLE USAGE FILE: {target_file} (line-numbered for reference only - do not include the numbers in your output)
--- BEGIN FILE CONTENT ---
{numbered}
--- END FILE CONTENT ---

Produce a fix that directly hardens this file's use of {finding.component} against the advisory above \
(for a JWT algorithm/key issue, that typically means explicitly restricting accepted algorithms on verify calls; \
use your judgment for other advisory types).

Instead of returning the whole file, return one or more surgical "replacements". Each replacement has:
- original_snippet: an EXACT, contiguous, verbatim substring copied from the file content above (without line \
number prefixes) - as short as possible while still being unique in the file, containing only the code you are \
changing plus the minimum surrounding context needed for uniqueness.
- replacement_snippet: the new code that replaces it.

Do not touch anything outside these snippets - do not reformat, do not "clean up", do not rewrap unrelated lines. \
Keep each original_snippet minimal and precise; it will be matched with a literal string search and must appear \
exactly once in the file.

Also write one new, real, runnable test file (in the same test framework style already used by this project if \
you can infer it, otherwise a plain Mocha/Chai or Jest test - infer from the file's own import style) that would \
fail against the original vulnerable code path and pass against your fix.

If the advisory is meaningfully mitigated by a dependency version bump, also give a package_json_version_bump \
semver range; otherwise null."""


def _correction_prompt(base_prompt: str, errors: list[str]) -> str:
    errors_text = "\n".join(f"- {e}" for e in errors)
    return f"""{base_prompt}

IMPORTANT CORRECTION: Your previous attempt produced original_snippet values that failed to match the file \
exactly once:
{errors_text}

Re-read the file content above carefully and re-generate the replacements, copying original_snippet verbatim \
(exact whitespace, exact characters, no line-number prefixes) from the file content shown."""


def _apply_replacements(original_content: str, replacements: list[dict]) -> tuple[str, list[str]]:
    """Applies each replacement via a single, exact str.replace. Returns the
    patched content and a list of error strings for any snippet that didn't
    match the current content exactly once (ambiguous or not found)."""
    content = original_content
    errors: list[str] = []
    for r in replacements:
        snippet = r["original_snippet"]
        count = content.count(snippet)
        if count == 0:
            errors.append(f"original_snippet not found verbatim in file: {snippet[:200]!r}")
            continue
        if count > 1:
            errors.append(f"original_snippet matched {count} times (not unique): {snippet[:200]!r}")
            continue
        content = content.replace(snippet, r["replacement_snippet"], 1)
    return content, errors


def _bump_package_json(repo_dir: Path, component: str, new_version: str) -> str | None:
    """Applies a real, targeted edit to package.json's pinned dependency
    range, via an exact string match on the current line (same
    find-and-replace discipline as _apply_replacements - never a full
    regeneration of package.json). Returns the old pinned range, or None if
    the component wasn't found under dependencies/devDependencies."""
    package_json_path = repo_dir / "package.json"
    text = package_json_path.read_text(encoding="utf-8")
    match = re.search(rf'"{re.escape(component)}"\s*:\s*"([^"]+)"', text)
    if match is None:
        return None
    old_version = match.group(1)
    old_line = match.group(0)
    new_line = f'"{component}": "{new_version}"'
    package_json_path.write_text(text.replace(old_line, new_line, 1), encoding="utf-8")
    return old_version


def generate_patch(
    finding: Finding,
    repo_dir: Path,
    max_attempts: int = 3,
    verification_feedback: str | None = None,
) -> tuple[PatchProposal, str]:
    """Returns (PatchProposal, patched_file_relative_path).

    Creates a real local git branch, writes the patched file + generated
    test to it, and commits - a genuine, inspectable artifact (`git show`
    on the branch proves it), not just text held in memory. Restores the
    original branch afterward so Hunter/Analyst keep scanning the
    pristine vulnerable snapshot on repeat runs; Re-Verifier is the one
    that checks the fix branch back out (see re_verifier.py).

    `verification_feedback`, when given, is real dynamic evidence from a
    prior Verification Lab run (e.g. "code-only algorithms restriction was
    proven insufficient against the pinned 0.4.0 release") - passed in by
    Re-Verifier when its first pass still finds the finding exploitable, so
    a second Patch Forge attempt is grounded in what was actually observed,
    not asked to guess blind a second time.
    """
    # Grounding gate: ensure a remediation pattern exists before generating
    cwe_id = (finding.cwe[0] if finding.cwe else "UNKNOWN").lower()
    language = "javascript"  # Infer from project (simplified here)
    pattern_lookup = retrieve_fix_pattern(cwe_id, language)
    if not pattern_lookup.get("pattern"):
        raise ValueError(
            f"No remediation pattern found for {cwe_id} in {language}. "
            f"Escalating to human review instead of improvising."
        )

    matches = find_reachability_evidence(repo_dir, finding.component)
    if not matches:
        raise ValueError(f"No reachable usage file found for {finding.component} - nothing to patch.")

    target_relpath = matches[0].file
    target_path = repo_dir / target_relpath
    original_content = target_path.read_text(encoding="utf-8")

    armor_result = model_armor.scan(original_content, source=target_relpath, agent="patch-forge")
    if not armor_result.clean:
        raise PermissionError(
            f"Model Armor blocked {target_relpath} before it reached the LLM prompt: {armor_result.findings}"
        )

    base_prompt = _build_prompt(
        finding, target_relpath, original_content,
        reference_pattern=str(pattern_lookup.get("pattern", ""))
    )
    if verification_feedback:
        base_prompt += f"""

DYNAMIC VERIFICATION FEEDBACK FROM A PRIOR ATTEMPT: {verification_feedback}
You MUST set package_json_version_bump to a real semver range that fixes this (do not leave it null)."""
    prompt = base_prompt
    parsed: dict | None = None
    patched_content = ""
    errors: list[str] = []

    for attempt in range(1, max_attempts + 1):
        raw = call_gemini(prompt, response_schema=_PATCH_SCHEMA, temperature=0.1)
        parsed = json.loads(raw)
        patched_content, errors = _apply_replacements(original_content, parsed["replacements"])
        if not errors:
            break
        if attempt < max_attempts:
            prompt = _correction_prompt(base_prompt, errors)

    if errors:
        raise ValueError(
            f"Patch Forge rejected its own output for {finding.finding_id} after {max_attempts} attempts - "
            f"snippet matching kept failing: {errors}"
        )

    assert parsed is not None
    diff = "".join(
        difflib.unified_diff(
            original_content.splitlines(keepends=True),
            patched_content.splitlines(keepends=True),
            fromfile=f"a/{target_relpath}",
            tofile=f"b/{target_relpath}",
        )
    )

    branch_name = f"sentinel/fix-{finding.finding_id.lower().replace('sentinel-f-', '')}"
    test_relpath = parsed["test_file_relative_path"]
    starting_branch = _current_branch(repo_dir)
    version_bump = parsed.get("package_json_version_bump")
    files_changed = [target_relpath]

    _git(repo_dir, "checkout", "-B", branch_name)
    try:
        target_path.write_text(patched_content, encoding="utf-8")
        test_path = repo_dir / test_relpath
        test_path.parent.mkdir(parents=True, exist_ok=True)
        test_path.write_text(parsed["test_file_content"], encoding="utf-8")

        add_paths = [target_relpath, test_relpath]
        if version_bump:
            old_version = _bump_package_json(repo_dir, finding.component, version_bump)
            if old_version is not None:
                files_changed.append("package.json")
                add_paths.append("package.json")

        _git(repo_dir, "add", *add_paths)
        commit_msg = f"fix({finding.component}): {finding.advisory_id} - Patch Forge remediation"
        if version_bump and "package.json" in files_changed:
            commit_msg += f" (bumps {finding.component} to {version_bump})"
        _git(repo_dir, "commit", "-m", commit_msg)
    finally:
        _git(repo_dir, "checkout", starting_branch)

    explanation = parsed["explanation"]
    if version_bump and "package.json" in files_changed:
        explanation += f" Additionally bumped {finding.component} to {version_bump} in package.json."

    proposal = PatchProposal(
        finding_id=finding.finding_id,
        branch_name=branch_name,
        files_changed=files_changed,
        diff=diff,
        generated_test_paths=[test_relpath],
        explanation=explanation,
    )
    return proposal, target_relpath


if __name__ == "__main__":
    from app.agents.analyst import analyze
    from app.agents.hunter import hunt

    repo = Path("workdir/juice-shop")
    findings = hunt(repo)
    target = next(f for f in findings if f.component == "jsonwebtoken")

    verdict = analyze(target, repo)
    print(f"Analyst verdict: {verdict.verdict.value}\n")

    proposal, changed_file = generate_patch(target, repo)
    print(f"Patch Forge branch: {proposal.branch_name}")
    print(f"Files changed: {proposal.files_changed}")
    print(f"Generated tests: {proposal.generated_test_paths}\n")
    print(f"Explanation: {proposal.explanation}\n")
    print("--- DIFF ---")
    print(proposal.diff)
