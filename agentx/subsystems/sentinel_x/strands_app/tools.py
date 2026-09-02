"""Wraps the shared, framework-agnostic tool functions (see
app/agent_tools.py) as real Strands Agents SDK @tool functions. Same
underlying real agent logic as app/adk_app/tools.py - only the SDK-facing
decoration differs.
"""

from __future__ import annotations

from strands import tool

from agentx.subsystems.sentinel_x import agent_tools

hunter_scan = tool(agent_tools.hunter_scan)
analyst_assess_relevance = tool(agent_tools.analyst_assess_relevance)
verification_lab_run_scenario = tool(agent_tools.verification_lab_run_scenario)
patch_forge_generate_patch = tool(agent_tools.patch_forge_generate_patch)
re_verifier_confirm_fix = tool(agent_tools.re_verifier_confirm_fix)
evidence_agent_seal_record = tool(agent_tools.evidence_agent_seal_record)
