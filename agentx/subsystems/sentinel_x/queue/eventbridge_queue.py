"""Real AWS EventBridge/SQS-backed queue. Activates once AWS credentials
exist (via `aws configure`, environment variables, or an attached IAM role)
and SENTINEL_QUEUE_BACKEND=eventbridge is set. Same Job semantics as
LocalQueue/PubSubQueue - only the transport differs.
"""

from __future__ import annotations

import json
import os

import boto3

from agentx.subsystems.sentinel_x.queue.base import Job, JobQueue
from agentx.subsystems.sentinel_x.queue.local_queue import LocalQueue

EVENT_BUS_NAME = os.environ.get("SENTINEL_EVENT_BUS", "sentinel-investigations")
QUEUE_URL = os.environ.get("SENTINEL_SQS_QUEUE_URL", "")


class EventBridgeQueue(JobQueue):
    """Publishes real events to EventBridge (for fan-out/observability) and
    reads work items back off a real SQS queue subscribed to that bus - the
    standard AWS async-worker pattern. Status/results are bookkept via
    LocalQueue until app/store/dynamodb_store.py is wired to a live table."""

    def __init__(self) -> None:
        if not QUEUE_URL:
            raise RuntimeError("SENTINEL_SQS_QUEUE_URL is not set - cannot use EventBridgeQueue.")
        self._events = boto3.client("events")
        self._sqs = boto3.client("sqs")
        self._local = LocalQueue()

    def enqueue(self, job_type: str, payload: dict) -> Job:
        job = Job.new(job_type, payload)
        self._local._write(job)  # noqa: SLF001
        self._events.put_events(
            Entries=[
                {
                    "Source": "sentinel.agents",
                    "DetailType": job_type,
                    "Detail": json.dumps({"job_id": job.job_id, "payload": payload}),
                    "EventBusName": EVENT_BUS_NAME,
                }
            ]
        )
        return job

    def claim_next(self) -> Job | None:
        response = self._sqs.receive_message(QueueUrl=QUEUE_URL, MaxNumberOfMessages=1, WaitTimeSeconds=1)
        messages = response.get("Messages", [])
        if not messages:
            return None
        message = messages[0]
        body = json.loads(message["Body"])
        detail = json.loads(body.get("detail", body.get("Detail", "{}")))
        self._sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=message["ReceiptHandle"])
        job = self._local.get(detail["job_id"])
        if job:
            job.status = "running"
            self._local._write(job)  # noqa: SLF001
        return job

    def complete(self, job_id: str, result: dict) -> None:
        self._local.complete(job_id, result)

    def fail(self, job_id: str, error: str) -> None:
        self._local.fail(job_id, error)

    def get(self, job_id: str) -> Job | None:
        return self._local.get(job_id)

    def list_jobs(self, limit: int = 50) -> list[Job]:
        return self._local.list_jobs(limit)
