"""Hunter - discovers findings by running a real dependency scanner
(`npm audit --json`) against an actual cloned repository. No finding here
is invented: every advisory ID, CVSS score, and CWE comes straight out of
npm's own audit report.

Grounding Gate: Before a finding reaches Analyst, Hunter resolves its ID
against OSV.dev, NVD, or GHSA. Three outcomes:
  - Resolved: attach real record, proceed
  - Ambiguous: multiple matches, attach all candidates, let Analyst disambiguate
  - Unresolved: mark UNVERIFIED, do not pass to Analyst as confirmed
"""

from __future__ import annotations

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from app.config import DEMO_REPO_DIR, DEMO_REPO_URL
from app.schemas import Finding, Severity
from app.grounded_tools import lookup_vulnerability
from app.knowledge import advisory_cache


def ensure_repo_cloned(repo_dir: Path = DEMO_REPO_DIR, repo_url: str = DEMO_REPO_URL) -> Path:
    """Clones the target repo once, shallowly. Idempotent - re-runs are a no-op
    if the directory already exists, so Hunter stays fast on repeat scans."""
    if (repo_dir / ".git").exists():
        return repo_dir

    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--depth", "1", repo_url, str(repo_dir)],
        check=True,
        capture_output=True,
        text=True,
    )
    return repo_dir


def _ensure_lockfile(repo_dir: Path) -> None:
    """npm audit hard-requires a lockfile ("loadVirtual requires existing
    shrinkwrap file") and refuses outright without one. This is not a
    hypothetical: the demo repo's own .gitignore excludes
    package-lock.json, so a fresh clone of it has none - discovered when a
    from-scratch container hung on its first scan with npm silently unable
    to proceed. Any real target repo can legitimately choose not to commit
    its lockfile, so this has to be handled generally, not patched around
    for one repo.

    --package-lock-only resolves the dependency tree into a lockfile
    without installing anything; --ignore-scripts is required alongside it
    - without it, npm runs the repo's own lifecycle scripts (juice-shop
    triggers a full frontend build), which is slow, may need tooling the
    scanning environment doesn't have, and has nothing to do with auditing
    dependencies."""
    if (repo_dir / "package-lock.json").exists():
        return
    subprocess.run(
        ["npm.cmd" if _is_windows() else "npm", "i", "--package-lock-only", "--ignore-scripts"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        # 120s was measured on a full-speed local machine (~16s) and looked
        # generous. It wasn't: on a shared free-tier host, real dependency
        # resolution for this repo's full tree took longer than that and hit
        # the timeout - "[startup] findings warm-up failed... timed out
        # after 120 seconds" was the actual failure seen live. This is a
        # one-time cost per fresh clone (the lockfile persists on disk once
        # written), so 300s buys real headroom for slow/contended hosts
        # without meaningfully changing the common case.
        timeout=300,
    )
    # Deliberately not checked for success here: if it failed, the repo
    # still has no lockfile, and the audit call right after this will raise
    # its own clear error - duplicating that check would just produce two
    # different error messages for the same underlying fact.


def run_npm_audit(repo_dir: Path) -> dict:
    """Shells out to the real npm CLI. npm audit exits non-zero whenever
    vulnerabilities are found, so we don't check the return code - we check
    that stdout is actually parseable JSON."""
    _ensure_lockfile(repo_dir)
    result = subprocess.run(
        ["npm.cmd" if _is_windows() else "npm", "audit", "--json"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if not result.stdout.strip():
        raise RuntimeError(f"npm audit produced no output. stderr: {result.stderr[:2000]}")
    return json.loads(result.stdout)


def _is_windows() -> bool:
    import platform

    return platform.system() == "Windows"


_SEVERITY_ALIASES = {"moderate": "medium", "info": "low"}


def _normalize_severity(raw: str) -> Severity:
    return Severity(_SEVERITY_ALIASES.get(raw, raw))


def parse_findings(audit_report: dict, source: str = "npm audit") -> list[Finding]:
    findings: list[Finding] = []
    vulnerabilities = audit_report.get("vulnerabilities", {})

    for package_name, entry in vulnerabilities.items():
        advisories = [v for v in entry.get("via", []) if isinstance(v, dict)]
        if not advisories:
            # "via" can list plain package-name strings (transitive-only
            # entries with no direct advisory) - nothing to report on its own.
            continue

        # Use the most severe advisory attached to this package as the
        # representative one; npm already sorts nothing here, so we pick by CVSS.
        primary = max(advisories, key=lambda a: (a.get("cvss") or {}).get("score") or 0)
        advisory_id = primary["url"].rstrip("/").split("/")[-1] if primary.get("url") else None

        findings.append(
            Finding(
                finding_id=f"SENTINEL-F-{advisory_id or package_name}",
                severity=_normalize_severity(entry.get("severity", "low")),
                component=package_name,
                version=entry.get("range", "unknown"),
                source=source,
                advisory_id=advisory_id,
                advisory_url=primary.get("url"),
                cwe=primary.get("cwe", []),
                cvss_score=(primary.get("cvss") or {}).get("score"),
                summary=primary.get("title"),
            )
        )

    return findings


@dataclass
class ScanStats:
    """How the last grounding pass went. The distinction that matters:
    a finding dropped because its advisory genuinely isn't in OSV/NVD/GHSA
    is a real result, but one dropped because the knowledge source was
    unreachable is a *degraded scan* - and callers must be able to tell
    those apart rather than both silently presenting as "no findings"."""

    raw: int = 0
    grounded: int = 0
    unresolved: int = 0
    errored: int = 0
    from_cache: int = 0

    @property
    def degraded(self) -> bool:
        """True when lookups failed outright, so this scan under-reports."""
        return self.errored > 0

    @property
    def served_entirely_from_cache(self) -> bool:
        """A scan can be complete and correct while never reaching OSV/NVD -
        cached records are real records we genuinely retrieved earlier, so
        this is not a degraded scan. It is still worth surfacing: if this is
        True for a long stretch it means the live sources have not actually
        been contacted, which is how an upstream outage hides in plain sight."""
        return self.raw > 0 and self.from_cache == self.grounded and self.grounded > 0


last_scan = ScanStats()

# Small on purpose: OSV/NVD are free public APIs.
_GROUNDING_CONCURRENCY = int(os.environ.get("SENTINEL_GROUNDING_CONCURRENCY", 8))


def _resolve_one(finding: Finding) -> tuple[Finding, dict]:
    """Resolve a single finding, preferring the cache. Kept separate so the
    gate below can run these concurrently without duplicating the
    cache-then-network logic."""
    cached = advisory_cache.get(finding.advisory_id)
    if cached is not None:
        return finding, {**cached, "_cache_hit": True}
    # Looked up as a module global (not captured at import) so tests that
    # monkeypatch hunter.lookup_vulnerability still intercept it here.
    result = lookup_vulnerability(finding.advisory_id)
    advisory_cache.put(finding.advisory_id, result)  # no-ops on failure
    return finding, result


def _apply_grounding_gate(findings: list[Finding]) -> list[Finding]:
    """Grounding gate: resolve each finding's advisory_id against real
    knowledge sources. Returns only confirmed findings; anything unresolved
    is marked UNVERIFIED and withheld from Analyst.

    Records the outcome in `last_scan` so a caller can distinguish "nothing
    was found" from "we could not reach OSV/NVD/GHSA to check".

    Lookups run concurrently. They are independent, pure-IO, and each cost
    ~0.85s against OSV, so doing 25 of them serially burned ~21s of wall
    clock per scan for no reason. The pool is deliberately small - these are
    free public APIs and hammering them with 25 simultaneous connections is
    how you earn a rate limit - and results are re-ordered back to match the
    input so the gate stays deterministic regardless of completion order.
    """
    global last_scan
    stats = ScanStats(raw=len(findings))

    resolvable = [f for f in findings if f.advisory_id]
    for finding in findings:
        if not finding.advisory_id:
            finding.grounding_status = "UNVERIFIED"
            stats.unresolved += 1

    outcomes: dict[str, dict] = {}
    if resolvable:
        workers = min(_GROUNDING_CONCURRENCY, len(resolvable))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="grounding") as pool:
            for finding, result in pool.map(_resolve_one, resolvable):
                outcomes[finding.finding_id] = result

    grounded: list[Finding] = []
    # Iterate the original list, not completion order, so two identical
    # scans always produce an identically ordered result.
    for finding in resolvable:
        lookup = outcomes.get(finding.finding_id) or {}
        if lookup.get("resolved"):
            if lookup.get("_cache_hit"):
                stats.from_cache += 1
            finding.verified_advisory_record = lookup.get("record")
            finding.grounding_source = lookup.get("source")
            finding.grounding_status = "VERIFIED"
            grounded.append(finding)
        else:
            finding.grounding_status = "UNVERIFIED"
            if lookup.get("error"):
                stats.errored += 1
            else:
                stats.unresolved += 1

    stats.grounded = len(grounded)
    last_scan = stats

    if stats.errored:
        print(
            f"[GROUNDING GATE] DEGRADED SCAN: {stats.errored}/{stats.raw} lookups failed "
            f"(knowledge source unreachable) - results under-report, not authoritative"
        )
    if stats.unresolved:
        print(f"[GROUNDING GATE] {stats.unresolved} findings unresolved in OSV/NVD/GHSA (marked UNVERIFIED)")
    return grounded


def hunt(repo_dir: Path | None = None) -> list[Finding]:
    """Full Hunter run: clone (if needed) -> scan -> parse -> apply grounding gate -> return Findings.
    Grounding gate filters out any findings that don't resolve in real knowledge sources."""
    target = ensure_repo_cloned() if repo_dir is None else repo_dir
    report = run_npm_audit(target)
    findings = parse_findings(report)
    return _apply_grounding_gate(findings)


if __name__ == "__main__":
    results = hunt()
    print(f"Hunter found {len(results)} real findings from npm audit:\n")
    for f in sorted(results, key=lambda x: x.cvss_score or 0, reverse=True)[:10]:
        print(f"  [{f.severity.value:>8}] {f.finding_id}  {f.component}  cvss={f.cvss_score}  {f.summary}")
