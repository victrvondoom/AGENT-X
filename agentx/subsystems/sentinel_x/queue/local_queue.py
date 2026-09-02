"""Real local file-based queue - no cloud dependency, works today. Jobs are
persisted to disk (survives process restart, which is the actual point:
enqueue an investigation, close your laptop, come back to a worker process
that picked it up and finished it while you were away) and claimed with a
real file lock so a concurrent worker never double-claims a job.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from agentx.subsystems.sentinel_x.config import WORKDIR
from agentx.subsystems.sentinel_x.queue.base import Job, JobQueue

QUEUE_DIR = WORKDIR / "queue"
QUEUE_DIR.mkdir(exist_ok=True, parents=True)


class LocalQueue(JobQueue):
    def _path(self, job_id: str) -> Path:
        return QUEUE_DIR / f"{job_id}.json"

    def _write(self, job: Job) -> None:
        self._path(job.job_id).write_text(json.dumps(asdict(job), indent=2), encoding="utf-8")

    def _read(self, path: Path) -> Job:
        return Job(**json.loads(path.read_text(encoding="utf-8")))

    def enqueue(self, job_type: str, payload: dict) -> Job:
        job = Job.new(job_type, payload)
        self._write(job)
        return job

    def claim_next(self) -> Job | None:
        for path in sorted(QUEUE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime):
            job = self._read(path)
            if job.status != "queued":
                continue
            lock_path = path.with_suffix(".lock")
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
            except FileExistsError:
                continue  # another worker already claimed it
            job.status = "running"
            job.updated_at = datetime.now(timezone.utc).isoformat()
            self._write(job)
            return job
        return None

    def complete(self, job_id: str, result: dict) -> None:
        job = self.get(job_id)
        if job is None:
            return
        job.status, job.result = "done", result
        job.updated_at = datetime.now(timezone.utc).isoformat()
        self._write(job)

    def fail(self, job_id: str, error: str) -> None:
        job = self.get(job_id)
        if job is None:
            return
        job.status, job.error = "failed", error
        job.updated_at = datetime.now(timezone.utc).isoformat()
        self._write(job)

    def get(self, job_id: str) -> Job | None:
        path = self._path(job_id)
        return self._read(path) if path.exists() else None

    def list_jobs(self, limit: int = 50) -> list[Job]:
        paths = sorted(QUEUE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        return [self._read(p) for p in paths[:limit]]
