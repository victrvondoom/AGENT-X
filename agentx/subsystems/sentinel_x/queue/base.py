from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4


@dataclass
class Job:
    job_id: str
    job_type: str  # e.g. "investigate_finding"
    payload: dict
    status: str = "queued"  # queued | running | done | failed
    result: dict | None = None
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @staticmethod
    def new(job_type: str, payload: dict) -> "Job":
        return Job(job_id=str(uuid4()), job_type=job_type, payload=payload)


class JobQueue(ABC):
    """Real interface a worker polls/subscribes to. Implementations differ
    only in transport (local file, Pub/Sub, EventBridge) - job semantics
    (enqueue, claim, complete, fail, status) are identical everywhere, so
    swapping backends never touches worker.py or the agents it calls."""

    @abstractmethod
    def enqueue(self, job_type: str, payload: dict) -> Job: ...

    @abstractmethod
    def claim_next(self) -> Job | None: ...

    @abstractmethod
    def complete(self, job_id: str, result: dict) -> None: ...

    @abstractmethod
    def fail(self, job_id: str, error: str) -> None: ...

    @abstractmethod
    def get(self, job_id: str) -> Job | None: ...

    @abstractmethod
    def list_jobs(self, limit: int = 50) -> list[Job]: ...
