"""A real, working guardrail against prompt injection and PII leakage from
untrusted repository content (file contents, commit messages, READMEs)
before it reaches an LLM prompt - a local, free-tier-friendly stand-in for
Vertex AI Model Armor / Bedrock Guardrails, with the same enforcement point
(scan before the content is ever concatenated into a prompt) and the same
swap-in seam: once real GCP/AWS access exists, `scan()` becomes a thin
wrapper around the managed API instead of local heuristics - callers never
change.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from app.config import WORKDIR

MODEL_ARMOR_LOG_PATH = WORKDIR / "model_armor_log.jsonl"

_INJECTION_PATTERNS = [
    re.compile(r"ignore (all|the|any) (previous|prior|above) instructions", re.IGNORECASE),
    re.compile(r"disregard (your|the) (system prompt|instructions)", re.IGNORECASE),
    re.compile(r"you are now|act as (if you are|a different)", re.IGNORECASE),
    re.compile(r"\bexfiltrate\b|\bsend .* to https?://", re.IGNORECASE),
    re.compile(r"reveal (your|the) (system prompt|api key|secret)", re.IGNORECASE),
]

_PII_PATTERNS = [
    ("email", re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private_key", re.compile(r"-----BEGIN (RSA |EC )?PRIVATE KEY-----")),
]


@dataclass
class ScanResult:
    clean: bool
    # "clean"   - nothing matched
    # "flagged" - PII found; allowed through, but a human should see it
    # "blocked" - injection attempt; never reaches the model
    #
    # `clean` and `severity` are deliberately separate: a flagged scan is
    # still clean in the sense that matters to the caller (it proceeds),
    # while remaining visibly different from one where nothing was found.
    # Collapsing the two made PII detections render identically to
    # untouched content, so nobody ever saw them.
    severity: str  # "clean" | "flagged" | "blocked"
    findings: list[str]


def _log_event(agent: str, source: str, result: ScanResult) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "source": source,
        "severity": result.severity,
        "text": "; ".join(result.findings) if result.findings else f"{source} scanned - clean",
    }
    with MODEL_ARMOR_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def read_log(limit: int = 200) -> list[dict]:
    if not MODEL_ARMOR_LOG_PATH.exists():
        return []
    lines = MODEL_ARMOR_LOG_PATH.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines[-limit:]]


def scan(content: str, *, source: str, agent: str = "unknown") -> ScanResult:
    """Scans real untrusted content (a file, a commit message, a README)
    before it's allowed into an LLM prompt. Injection attempts are blocked
    outright; PII matches are flagged but don't block (the content may
    legitimately need review) - same severity model the Guardrails page
    displays. Every real scan is persisted to MODEL_ARMOR_LOG_PATH so the
    Governance page's guardrail feed reflects actual scans, not fixtures."""
    findings: list[str] = []

    for pattern in _INJECTION_PATTERNS:
        if pattern.search(content):
            # The pattern itself, not its repr: repr() doubles every
            # backslash, so the UI rendered '\bexfiltrate\b' where the
            # actual pattern is 'exfiltrate'. Quoted so the feed can
            # still split the sentence from the pattern.
            findings.append(f"prompt injection pattern matched in {source}: '{pattern.pattern}'")

    if findings:
        result = ScanResult(clean=False, severity="blocked", findings=findings)
        _log_event(agent, source, result)
        return result

    pii_hits = []
    for label, pattern in _PII_PATTERNS:
        if pattern.search(content):
            pii_hits.append(label)
    if pii_hits:
        findings.append(f"PII pattern(s) detected in {source}: {', '.join(pii_hits)}")

    # clean=True either way - PII does not block. Only the label differs, so
    # the Governance feed can show a reviewer that something was found.
    result = ScanResult(
        clean=True,
        severity="flagged" if pii_hits else "clean",
        findings=findings,
    )
    _log_event(agent, source, result)
    return result
