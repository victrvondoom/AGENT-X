"""Evidence Agent - assembles the canonical EvidenceObject for a finding from
the real outputs of the other agents (Hunter, Analyst, Patch Forge, and -
once available - Verification Lab / Re-Verifier), and computes a real
cryptographic signature over the assembled record so it can't be silently
altered afterward. No fabricated data: every field here is either copied
directly from another agent's real output or computed (hash, timestamp,
git commit lookup).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from agentx.subsystems.sentinel_x.config import WORKDIR
from agentx.subsystems.sentinel_x.integrations import nutrient_dws
from agentx.subsystems.sentinel_x.schemas import (
    EvidenceObject,
    Finding,
    PatchProposal,
    RelevanceVerdict,
    TimelineEntry,
    VerificationResult,
)

EVIDENCE_DIR = WORKDIR / "evidence"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sign(payload: dict) -> str:
    """Real SHA-256 digest over the canonical (sorted-key) JSON serialization
    of the evidence payload - content-addressed, so any later tampering with
    the record changes the signature. This stands in for the DWS seal until
    the Nutrient DWS integration (build-order step 6) is wired in."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def verify_signature(evidence: dict) -> bool:
    """Real tamper check: recompute the signature from the record's current
    content and compare against the stored one. Used by /api/health so the
    Audit Persistence Monitor's integrity number means something (if a file
    in the evidence store were hand-edited without going through this
    module, this is what would catch it)."""
    payload = {
        "finding_id": evidence.get("finding_id"),
        "repo": evidence.get("repo"),
        "commit": evidence.get("commit"),
        "timeline": evidence.get("timeline", []),
        "final_status": evidence.get("final_status"),
        "verdict": evidence.get("verdict"),
        "verification_results": evidence.get("verification_results", []),
        "patch_proposal": evidence.get("patch_proposal"),
    }
    return _sign(payload) == evidence.get("signature")


def render_report_html(payload: dict, signature: str) -> str:
    """Renders the sealed evidence payload as a real, self-contained HTML
    document. This is what Nutrient DWS /build converts to the PDF that
    /sign then seals - DWS can only sign a document, and the Evidence
    Agent's native output is JSON, so a real document has to exist first."""
    def esc(v: object) -> str:
        return (
            str(v)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    rows = "".join(
        f"<tr><td>{esc(t['actor'])}</td><td>{esc(t['action'])}</td><td>{esc(t['ts'])}</td></tr>"
        for t in payload.get("timeline", [])
    )
    verification = "".join(
        f"<li><b>{esc(vr['scenario'])}</b> &rarr; {esc(vr['result'])} "
        f"(sandbox {esc(vr['sandbox_id'])}, {esc(vr['duration_ms'])}ms)<br/>{esc(vr['observed'])}</li>"
        for vr in payload.get("verification_results", [])
    )
    verdict = payload.get("verdict") or {}
    patch = payload.get("patch_proposal") or {}

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>SENTINEL Evidence Report {esc(payload['finding_id'])}</title>
<style>
body{{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;margin:40px;color:#111;line-height:1.5}}
h1{{font-size:20px;margin-bottom:4px}} h2{{font-size:14px;margin-top:24px;border-bottom:1px solid #ddd;padding-bottom:4px}}
table{{border-collapse:collapse;width:100%;font-size:11px}} td,th{{border:1px solid #ddd;padding:6px;text-align:left;vertical-align:top}}
code,pre{{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:10px;background:#f6f6f6;padding:8px;display:block;white-space:pre-wrap;word-break:break-all}}
.meta{{font-size:11px;color:#555}} ul{{font-size:11px;padding-left:18px}} li{{margin-bottom:8px}}
</style></head><body>
<h1>SENTINEL Evidence Report</h1>
<p class="meta">{esc(payload['finding_id'])} &middot; {esc(payload.get('repo'))} &middot;
final status: <b>{esc(payload.get('final_status'))}</b></p>

<h2>Relevance verdict</h2>
<p class="meta">{esc(verdict.get('verdict', 'not recorded'))}</p>
<p style="font-size:11px">{esc(verdict.get('reasoning', ''))}</p>

<h2>Sandbox verification</h2>
<ul>{verification or "<li>No scenarios recorded.</li>"}</ul>

<h2>Remediation</h2>
<p class="meta">branch: {esc(patch.get('branch_name', 'none'))} &middot;
files: {esc(", ".join(patch.get('files_changed', [])) or "none")}</p>
<pre>{esc(patch.get('diff', 'No patch generated.'))}</pre>

<h2>Audit trail</h2>
<table><tr><th>Actor</th><th>Action</th><th>Timestamp</th></tr>{rows}</table>

<h2>Cryptographic signature</h2>
<pre>{esc(signature)}</pre>
<p class="meta">SHA-256 over the canonical JSON of this record. Any change to the
content above changes this value.</p>
</body></html>"""


def signed_pdf_path(finding_id: str, dws_seal: str | None) -> Path | None:
    """Resolve the exact signed PDF a record's seal refers to.

    Prefers the immutable, digest-named archive so an old record keeps
    resolving to the artifact it was actually sealed with, even after the
    finding has been re-investigated. Falls back to the legacy
    "<finding_id>.signed.pdf" for records sealed before archiving existed.
    """
    if dws_seal:
        digest = dws_seal.rsplit(":", 1)[-1]
        archived = EVIDENCE_DIR / f"{finding_id}.{digest[:16]}.signed.pdf"
        if archived.exists():
            return archived
    legacy = EVIDENCE_DIR / f"{finding_id}.signed.pdf"
    return legacy if legacy.exists() else None


def _archive_signed_pdf(finding_id: str, signed_path: Path, sha256: str) -> None:
    """Keep an immutable copy named after the artifact's own digest."""
    try:
        archived = EVIDENCE_DIR / f"{finding_id}.{sha256[:16]}.signed.pdf"
        if not archived.exists():
            archived.write_bytes(signed_path.read_bytes())
    except OSError as exc:  # noqa: BLE001 - archiving must not lose the seal
        print(f"[evidence-agent] could not archive signed PDF for {finding_id}: {exc}")


def _maybe_dws_seal(finding_id: str, payload: dict, signature: str) -> str | None:
    """Seals the Evidence Report through the real Nutrient DWS pipeline
    (/build to PDF, then /sign) when NUTRIENT_API_KEY is configured.

    Returns None when DWS isn't configured - the record still carries its
    own real SHA-256 signature, and the UI reports "SHA-256 sealed" rather
    than claiming a DWS seal that was never issued. A DWS failure is
    logged and also yields None, never a fabricated seal.
    """
    if not nutrient_dws.is_configured():
        return None
    try:
        html = render_report_html(payload, signature)
        pdf_path = EVIDENCE_DIR / f"{finding_id}.pdf"
        signed_path = EVIDENCE_DIR / f"{finding_id}.signed.pdf"
        result = nutrient_dws.seal_evidence_document(html, str(pdf_path), str(signed_path))
        # Archive the artifact under its own digest as well.
        #
        # Both paths above are keyed only by finding_id, so re-investigating
        # a finding overwrites the previous run's signed PDF. That silently
        # orphans every earlier record: its dws_seal still names a digest,
        # but the only file on disk is now a different run's artifact, and
        # verification reports a mismatch that looks exactly like tampering.
        # Naming the archived copy after its own content makes each seal
        # permanently retrievable and immutable, which is the property the
        # whole evidence claim rests on.
        _archive_signed_pdf(finding_id, signed_path, result["sha256"])
        # The seal reference is the SHA-256 of the actual signed PDF that DWS
        # returned. That identifies the exact issued artifact and anyone
        # holding the file can recompute it - unlike an opaque server-side id
        # that a reader has no way to check.
        return f"dws:sha256:{result['sha256']}"
    except Exception as exc:  # noqa: BLE001 - a seal failure must not lose the evidence record
        print(f"[evidence-agent] Nutrient DWS seal failed for {finding_id}: {exc}")
        return None


def _resolve_commit(repo_dir: Path | None, branch_name: str | None) -> str | None:
    if repo_dir is None or branch_name is None:
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", branch_name],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return None


def assemble_evidence(
    finding: Finding,
    repo: str,
    verdict: RelevanceVerdict | None = None,
    verification_results: list[VerificationResult] | None = None,
    patch_proposal: PatchProposal | None = None,
    repo_dir: Path | None = None,
) -> EvidenceObject:
    verification_results = verification_results or []

    timeline: list[TimelineEntry] = [
        TimelineEntry(
            actor="Hunter",
            action=f"Detected {finding.advisory_id} in {finding.component}@{finding.version} (severity: {finding.severity.value})",
            ts=_now(),
        )
    ]
    if verdict is not None:
        timeline.append(
            TimelineEntry(
                actor="Analyst",
                action=f"Relevance verdict: {verdict.verdict.value} - {verdict.reasoning}",
                ts=_now(),
            )
        )
    for vr in verification_results:
        timeline.append(
            TimelineEntry(
                actor="Verification Lab",
                action=f"Scenario '{vr.scenario}' -> {vr.result} (sandbox {vr.sandbox_id}, {vr.duration_ms}ms)",
                ts=_now(),
            )
        )
    if patch_proposal is not None:
        timeline.append(
            TimelineEntry(
                actor="Patch Forge",
                action=f"Generated fix on {patch_proposal.branch_name} touching {patch_proposal.files_changed}: {patch_proposal.explanation}",
                ts=_now(),
            )
        )

    if verification_results and verification_results[-1].result == "RESOLVED":
        final_status = "RESOLVED"
    elif verification_results and verification_results[-1].result == "CONFIRMED_EXPLOITABLE":
        final_status = "CONFIRMED_EXPLOITABLE"
    elif patch_proposal is not None:
        final_status = "PATCH_PROPOSED"
    elif verdict is not None:
        final_status = f"ANALYZED_{verdict.verdict.value.upper()}"
    else:
        final_status = "DETECTED"

    commit = _resolve_commit(repo_dir, patch_proposal.branch_name if patch_proposal else None)

    payload = {
        "finding_id": finding.finding_id,
        "repo": repo,
        "commit": commit,
        "timeline": [t.model_dump() for t in timeline],
        "final_status": final_status,
        "verdict": verdict.model_dump() if verdict else None,
        "verification_results": [vr.model_dump() for vr in verification_results],
        "patch_proposal": patch_proposal.model_dump() if patch_proposal else None,
    }
    signature = _sign(payload)

    evidence = EvidenceObject(
        finding_id=finding.finding_id,
        repo=repo,
        commit=commit,
        timeline=timeline,
        final_status=final_status,
        signature=signature,
        dws_seal=_maybe_dws_seal(finding.finding_id, payload, signature),
        verdict=verdict,
        verification_results=verification_results,
        patch_proposal=patch_proposal,
    )

    out_path = EVIDENCE_DIR / f"{finding.finding_id}.json"
    out_path.write_text(evidence.model_dump_json(indent=2), encoding="utf-8")
    return evidence


if __name__ == "__main__":
    from agentx.subsystems.sentinel_x.agents.analyst import analyze
    from agentx.subsystems.sentinel_x.agents.hunter import hunt
    from agentx.subsystems.sentinel_x.agents.patch_forge import generate_patch
    from agentx.subsystems.sentinel_x.agents.re_verifier import reverify

    repo_dir = Path("workdir/juice-shop")
    findings = hunt(repo_dir)
    target = next(f for f in findings if f.component == "jsonwebtoken")

    verdict = analyze(target, repo_dir)
    proposal, _ = generate_patch(target, repo_dir)
    verification_results, final_proposal = reverify(target, repo_dir, proposal)

    evidence = assemble_evidence(
        finding=target,
        repo="juice-shop/juice-shop",
        verdict=verdict,
        verification_results=verification_results,
        patch_proposal=final_proposal,
        repo_dir=repo_dir,
    )

    print(f"finding_id: {evidence.finding_id}")
    print(f"final_status: {evidence.final_status}")
    print(f"commit: {evidence.commit}")
    print(f"signature: {evidence.signature}\n")
    print("--- TIMELINE ---")
    for entry in evidence.timeline:
        print(f"[{entry.ts}] {entry.actor}: {entry.action}")
    print(f"\nWritten to: {EVIDENCE_DIR / (evidence.finding_id + '.json')}")
