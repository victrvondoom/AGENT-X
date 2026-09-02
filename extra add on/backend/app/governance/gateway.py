"""The Agent Gateway: the single real enforcement point every tool call in
agent_tools.py passes through. Checks the Agent Registry (is this agent
approved to run at all?) and Agent Identity (is this specific action in
this agent's scope?) before letting the call proceed, and appends a real,
persisted log entry for every decision - allowed, blocked, or
requires-human. This is what the Governance & Control Plane's "live log of
tool calls" is actually built from once wired to the frontend, not a
narrative approximation of one.
"""

from __future__ import annotations

import functools
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TypeVar

from app.config import WORKDIR
from app.governance import identity, registry

GATEWAY_LOG_PATH = WORKDIR / "gateway_log.jsonl"

F = TypeVar("F", bound=Callable)


class GatewayDenied(PermissionError):
    pass


def _log(entry: dict) -> None:
    with GATEWAY_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def read_log(limit: int = 200) -> list[dict]:
    if not GATEWAY_LOG_PATH.exists():
        return []
    lines = GATEWAY_LOG_PATH.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines[-limit:]]


def enforce(agent_id: str, action: str) -> Callable[[F], F]:
    """Decorator: wraps a tool function so every real call is checked
    against the real Registry + Identity tables before it runs, and every
    decision (allowed/blocked) is appended to a real, readable log."""

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            ts = datetime.now(timezone.utc).isoformat()
            if not registry.is_approved(agent_id):
                _log({"ts": ts, "agent": agent_id, "action": action, "decision": "blocked",
                      "reason": f"agent '{agent_id}' is not approved in the Agent Registry"})
                raise GatewayDenied(f"Gateway denied: agent '{agent_id}' is not approved.")
            if not identity.is_permitted(agent_id, action):
                _log({"ts": ts, "agent": agent_id, "action": action, "decision": "blocked",
                      "reason": f"action '{action}' is outside '{agent_id}' scoped permissions"})
                raise GatewayDenied(f"Gateway denied: '{agent_id}' is not permitted to '{action}'.")

            _log({"ts": ts, "agent": agent_id, "action": action, "decision": "allowed", "reason": "policy check passed"})
            return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
