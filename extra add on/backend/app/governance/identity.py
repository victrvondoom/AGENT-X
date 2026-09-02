"""Real Agent Identity: least-privilege permission scopes per agent. The
Gateway checks these before allowing an action - this is what makes
"Hunter: read repo only" a real, enforced constraint rather than a claim in
a diagram. No agent has "deploy_production"; nothing in this table ever
grants it, and the Gateway has no code path that would honor it even if a
scope map were edited to add it (see gateway.py's DEPLOY_ACTIONS check).
"""

from __future__ import annotations

# action name -> agent ids allowed to perform it
PERMISSIONS: dict[str, set[str]] = {
    "read_repo": {"hunter", "analyst", "verifier", "patch-forge", "re-verifier"},
    "run_npm_audit": {"hunter"},
    "call_llm": {"analyst", "patch-forge"},
    "execute_sandbox": {"verifier", "re-verifier"},
    "create_branch": {"patch-forge"},
    "commit": {"patch-forge"},
    "trigger_patch": {"re-verifier"},
    "read_agent_logs": {"watchdog"},
    "raise_alert": {"watchdog"},
    "create_pull_request": {"patch-forge"},
    "write_evidence": {"evidence-agent"},
}

# Never in PERMISSIONS for any agent - deploying to production always routes
# to the human Deployment Gate, with no autonomous code path that can do it.
# Internal callers use the snake_case ids; the policy simulator accepts
# free-text human phrasing too (see _is_deploy_action below), since a real
# operator typing into a policy console won't type the exact enum value.
DEPLOY_ACTIONS = {"deploy_production", "merge_to_default_branch"}
_DEPLOY_ACTION_PHRASES = (
    "deploy production",
    "deploy to prod",
    "deploy_production",
    "merge to main",
    "merge to default branch",
    "merge_to_default_branch",
    "push to main",
    "push to production",
)


def _is_deploy_action(action: str) -> bool:
    normalized = action.strip().lower()
    if normalized in DEPLOY_ACTIONS:
        return True
    return any(phrase in normalized for phrase in _DEPLOY_ACTION_PHRASES)


def is_permitted(agent_id: str, action: str) -> bool:
    if _is_deploy_action(action):
        return False
    return agent_id in PERMISSIONS.get(action, set())


def evaluate(agent_id: str, action: str) -> tuple[str, str]:
    """Same classification the real Gateway enforces (is_permitted + the
    deploy-action check above), but returned as a 3-way decision instead of
    a bare bool, so a policy simulator can show *why* - and specifically
    distinguish "outside your scope" from "no agent may ever do this
    autonomously, it always routes to a human" - using this exact function,
    not a separate reimplementation."""
    if _is_deploy_action(action):
        return "requires_human", (
            f"'{action}' always routes to the human Deployment Gate - "
            "no agent has autonomous production-deploy access"
        )
    if action not in PERMISSIONS:
        return "blocked", f"'{action}' is not a recognized action in any agent's permission scope"
    if agent_id in PERMISSIONS[action]:
        return "allowed", f"'{agent_id}' is permitted to '{action}'"
    return "blocked", f"'{action}' is outside '{agent_id}' scoped permissions"
