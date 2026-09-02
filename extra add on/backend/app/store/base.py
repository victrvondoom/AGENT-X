from __future__ import annotations

from abc import ABC, abstractmethod


class EvidenceStore(ABC):
    @abstractmethod
    def put_evidence(self, finding_id: str, evidence: dict) -> None: ...

    @abstractmethod
    def get_evidence(self, finding_id: str) -> dict | None: ...

    @abstractmethod
    def list_evidence(self) -> list[dict]: ...
