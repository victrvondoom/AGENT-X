"""Real, tiny persisted store for human Deployment Gate decisions
(approve/reject per finding_id). Command Center's Evidence Vault panel
reads this for its "human review: pending/approved/rejected" badge, and the
Deployment Gate page writes to it - both read/write the same file, so
there's exactly one source of truth for "has a human signed off on this."
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.config import WORKDIR

DECISIONS_PATH = WORKDIR / "decisions.json"


def _load() -> dict:
    if not DECISIONS_PATH.exists():
        return {}
    return json.loads(DECISIONS_PATH.read_text(encoding="utf-8"))


def _save(data: dict) -> None:
    DECISIONS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_decision(finding_id: str) -> dict | None:
    return _load().get(finding_id)


def set_decision(finding_id: str, decision: str, actor: str = "operator") -> dict:
    if decision not in ("approved", "rejected"):
        raise ValueError(f"Invalid decision: {decision!r} (must be 'approved' or 'rejected')")
    data = _load()
    record = {
        "finding_id": finding_id,
        "decision": decision,
        "actor": actor,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    data[finding_id] = record
    _save(data)
    return record
