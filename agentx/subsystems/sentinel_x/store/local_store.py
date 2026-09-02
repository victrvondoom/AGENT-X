from __future__ import annotations

import json

from agentx.subsystems.sentinel_x.config import WORKDIR
from agentx.subsystems.sentinel_x.store.base import EvidenceStore

EVIDENCE_DIR = WORKDIR / "evidence"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)


class LocalJsonStore(EvidenceStore):
    """The same directory app/agents/evidence_agent.py already writes real
    signed evidence records to - this class just gives it the common
    EvidenceStore interface so worker.py/API routes can be backend-agnostic."""

    def put_evidence(self, finding_id: str, evidence: dict) -> None:
        (EVIDENCE_DIR / f"{finding_id}.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")

    def get_evidence(self, finding_id: str) -> dict | None:
        path = EVIDENCE_DIR / f"{finding_id}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def list_evidence(self) -> list[dict]:
        return [json.loads(p.read_text(encoding="utf-8")) for p in EVIDENCE_DIR.glob("*.json")]
