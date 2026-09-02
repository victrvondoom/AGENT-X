"""Real Agent Registry: every agent has an id, version, owner, and approval
status, persisted to disk (not just displayed in the UI as static copy).
The Gateway (gateway.py) actually checks this before letting a tool call
through - an unapproved or unknown agent id is rejected, for real.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.config import WORKDIR

REGISTRY_PATH = WORKDIR / "governance_registry.json"


@dataclass
class RegisteredAgent:
    id: str
    name: str
    version: str
    status: str  # "approved" | "in_review"
    owner: str
    capabilities: list[str] = field(default_factory=list)


_DEFAULT_AGENTS = [
    RegisteredAgent("hunter", "Hunter", "v1.0", "approved", "sentinel-core", ["read_repo", "run_npm_audit"]),
    RegisteredAgent("analyst", "Analyst", "v1.0", "approved", "sentinel-core", ["read_repo", "read_findings", "call_llm"]),
    RegisteredAgent("verifier", "Verification Lab", "v1.0", "approved", "sentinel-core", ["read_repo", "execute_sandbox"]),
    RegisteredAgent("patch-forge", "Patch Forge", "v1.0", "approved", "sentinel-core", ["read_repo", "create_branch", "commit", "call_llm"]),
    RegisteredAgent("re-verifier", "Re-Verifier", "v1.0", "approved", "sentinel-core", ["read_repo", "execute_sandbox", "trigger_patch"]),
    RegisteredAgent("watchdog", "Watchdog", "v0.1", "in_review", "sentinel-core", ["read_agent_logs", "raise_alert"]),
    RegisteredAgent("evidence-agent", "Evidence Agent", "v1.0", "approved", "sentinel-core", ["write_evidence"]),
]


def _load() -> dict[str, RegisteredAgent]:
    if not REGISTRY_PATH.exists():
        registry = {a.id: a for a in _DEFAULT_AGENTS}
        _save(registry)
        return registry
    raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    return {aid: RegisteredAgent(**data) for aid, data in raw.items()}


def _save(registry: dict[str, RegisteredAgent]) -> None:
    REGISTRY_PATH.write_text(
        json.dumps({aid: asdict(a) for aid, a in registry.items()}, indent=2), encoding="utf-8"
    )


def get_agent(agent_id: str) -> RegisteredAgent | None:
    return _load().get(agent_id)


def is_approved(agent_id: str) -> bool:
    agent = get_agent(agent_id)
    return agent is not None and agent.status == "approved"


def list_agents() -> list[RegisteredAgent]:
    return list(_load().values())


def set_status(agent_id: str, status: str) -> None:
    registry = _load()
    if agent_id in registry:
        registry[agent_id].status = status
        _save(registry)
