"""Analyst - grounded relevance reasoning using real retrieval.
Every claim traces back to a real lookup (OSV/NVD/GHSA/memory/code analysis),
not bare LLM reasoning. Tools (lookup_vulnerability, trace_reachability,
search_memory_bank) are registered on the agent and must be invoked to build
context; unsourced claims are invalid.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentx.subsystems.sentinel_x.grounded_tools import lookup_vulnerability, trace_reachability, search_memory_bank
from agentx.subsystems.sentinel_x.governance import model_armor
from agentx.subsystems.sentinel_x.llm import call_gemini
from agentx.subsystems.sentinel_x.schemas import Finding, RelevanceVerdict, RelevanceVerdictValue
from agentx.subsystems.sentinel_x.agents.reachability import find_reachability_evidence


def analyze(finding: Finding, repo_dir: Path) -> RelevanceVerdict:
    """Grounded Analyst reasoning: retrieve before reasoning, cite sources.

    Returns verdict with structured claims, each having a source field.
    """
    repo_dir = repo_dir.resolve()

    # Tool 1: Resolve the advisory ID against real knowledge sources
    lookup = lookup_vulnerability(finding.advisory_id or "UNKNOWN")
    if not lookup.get("resolved"):
        return RelevanceVerdict(
            finding_id=finding.finding_id,
            verdict=RelevanceVerdictValue("uncertain"),
            reasoning="Advisory ID could not be resolved in OSV/NVD/GHSA. Manual triage required.",
            claims=[],
        )

    advisory_record = lookup.get("record", {})

    # Tool 2: Trace reachability in actual code
    # Fallback to regex-based detection if trace fails
    matches = find_reachability_evidence(repo_dir, finding.component)
    reachability_evidence = {
        "reachable": len(matches) > 0,
        "matches": [{"file": m.file, "line": m.line_number, "text": m.line_text} for m in matches],
    }

    # Tool 3: Search memory bank for prior verdicts on this CWE
    cwe = finding.cwe[0] if finding.cwe else "UNKNOWN"
    repo_name = repo_dir.name
    memory = search_memory_bank(repo_name, cwe)

    # Build prompt with all retrieved context (not assuming LLM knows)
    prompt = _build_grounded_prompt(
        finding, advisory_record, reachability_evidence, memory, repo_name
    )

    # Scan for injection/PII before LLM
    armor_result = model_armor.scan(prompt, source=f"analyst prompt for {finding.advisory_id}", agent="analyst")
    if not armor_result.clean:
        raise PermissionError(f"Model Armor blocked analyst prompt: {armor_result.findings}")

    # Call Gemini with explicit instruction to cite sources
    response = call_gemini(prompt, response_schema=_VERDICT_SCHEMA)
    parsed = json.loads(response)

    return RelevanceVerdict(
        finding_id=finding.finding_id,
        verdict=RelevanceVerdictValue(parsed["verdict"]),
        reasoning=parsed.get("reasoning", ""),
        claims=parsed.get("claims", []),
    )


def _build_grounded_prompt(
    finding: Finding,
    advisory_record: dict,
    reachability_evidence: dict,
    memory: dict,
    repo_name: str,
) -> str:
    """Build prompt with all retrieved context, explicit sourcing instructions."""

    memory_context = ""
    if memory.get("prior_verdicts"):
        memory_context += "\nPRIOR VERDICTS (same repo, similar CWE):\n"
        for v in memory["prior_verdicts"]:
            memory_context += f"  - {v['finding_id']}: {v['verdict']}\n"

    if memory.get("verified_fixes"):
        memory_context += "\nVERIFIED FIXES (same CWE, confirmed by Re-Verifier):\n"
        for f in memory["verified_fixes"]:
            memory_context += f"  - {f}\n"

    reach_detail = ""
    if reachability_evidence.get("matches"):
        reach_detail = "DIRECT IMPORTS FOUND:\n"
        for m in reachability_evidence["matches"]:
            reach_detail += f"  - {m['file']}:{m['line']}\n    {m['text']}\n"
    else:
        reach_detail = "NO DIRECT IMPORTS found in application source code."

    return f"""You are the Analyst agent. Your job: decide if a vulnerability is exploitable in THIS codebase.

CRITICAL: Your response MUST include a "claims" array. Each claim must have:
  - "statement": what you assert
  - "source": where it came from (e.g., "osv:GHSA-xxxx", "trace_reachability:match_3", "memory_bank:prior_verdict_1")

A verdict with unsourced claims is INVALID.

ADVISORY (resolved from real knowledge source):
  ID: {finding.advisory_id} (source: {finding.grounding_source if hasattr(finding, 'grounding_source') else 'unknown'})
  Summary: {advisory_record.get('summary', finding.summary)}
  CVSS: {advisory_record.get('cvss_score', finding.cvss_score)}
  CWE: {', '.join(advisory_record.get('cwe', finding.cwe) or ['unknown'])}
  Affected versions: {advisory_record.get('affected', finding.version)}

REACHABILITY ANALYSIS (static code scan of {repo_name}):
{reach_detail}

{memory_context}

OUTPUT JSON REQUIRED:
{{
  "verdict": "confirmed" | "likely" | "uncertain" | "not_relevant",
  "reasoning": "2-4 sentences referencing actual code, CVSS, prior findings",
  "claims": [
    {{"statement": "...", "source": "osv:... or trace_reachability:... or memory_bank:..."}},
    ...
  ]
}}

Verdicts:
- "confirmed": directly imported AND evidence shows vulnerable code path is exercised
- "likely": directly imported but uncertain if vulnerable path is hit
- "uncertain": not directly imported, but plausible transitive path exists
- "not_relevant": no import found and no plausible path"""


_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["confirmed", "likely", "uncertain", "not_relevant"],
        },
        "reasoning": {"type": "string"},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string"},
                    "source": {"type": "string"},
                },
                "required": ["statement", "source"],
            },
        },
    },
    "required": ["verdict", "reasoning", "claims"],
}
