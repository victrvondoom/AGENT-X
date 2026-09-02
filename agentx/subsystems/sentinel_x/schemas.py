"""Pydantic schemas shared across every agent module.

These shapes are the actual contract with the frontend (see AGENTS.md
section 9 for the evidence object, and the individual agent I/O tables in
the backend build prompt). Nothing here is decorative - every field is
read by a real page once the data-contract wiring step lands.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Severity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class Finding(BaseModel):
    finding_id: str
    severity: Severity
    component: str
    version: str
    source: str
    # Real, verifiable advisory identifier (e.g. a GitHub Security Advisory
    # GHSA-id). Never a fabricated CVE-shaped string - see AGENTS.md's
    # "Demo data" note. Not every disclosed vulnerability has a CVE number
    # assigned, so this is deliberately advisory-scheme-agnostic.
    advisory_id: str | None = None
    advisory_url: str | None = None
    cwe: list[str] = Field(default_factory=list)
    cvss_score: float | None = None
    summary: str | None = None
    # Grounding fields (populated by Hunter's grounding gate)
    verified_advisory_record: dict | None = None
    grounding_source: str | None = None  # "osv", "nvd", "ghsa", etc.
    grounding_status: str | None = None  # "VERIFIED", "UNVERIFIED", etc.


class RelevanceVerdictValue(str, Enum):
    confirmed = "confirmed"
    likely = "likely"
    uncertain = "uncertain"
    not_relevant = "not_relevant"


class RelevanceVerdict(BaseModel):
    finding_id: str
    verdict: RelevanceVerdictValue
    reasoning: str
    # Sourced claims: each assertion must cite its source (osv, ghsa, trace_reachability, memory_bank)
    claims: list[dict[str, str]] = Field(default_factory=list)


VerificationOutcome = Literal["CONFIRMED_EXPLOITABLE", "RESOLVED", "INCONCLUSIVE"]


class VerificationResult(BaseModel):
    finding_id: str
    scenario: str
    expected: str
    observed: str
    result: VerificationOutcome
    sandbox_id: str
    duration_ms: int


class PatchProposal(BaseModel):
    finding_id: str
    branch_name: str
    files_changed: list[str]
    diff: str
    generated_test_paths: list[str]
    explanation: str


class TimelineEntry(BaseModel):
    actor: str
    action: str
    ts: str


class EvidenceObject(BaseModel):
    finding_id: str
    repo: str
    commit: str | None = None
    timeline: list[TimelineEntry] = Field(default_factory=list)
    final_status: str
    signature: str | None = None
    dws_seal: str | None = None
    # Structured sub-objects, retained verbatim from the pipeline stage that
    # produced them (not re-derived from the narrative timeline strings
    # above). Evidence Report, Remediation Forge's Verify tab, and the Audit
    # Ledger all need the actual verdict/verification/patch data, not just a
    # prose summary of it - these are what make this a real evidence
    # envelope instead of a narrative log with a signature bolted on.
    verdict: RelevanceVerdict | None = None
    verification_results: list[VerificationResult] = Field(default_factory=list)
    patch_proposal: PatchProposal | None = None
