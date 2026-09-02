"""Real Google Cloud Pub/Sub-backed queue. Requires GCP_PROJECT_ID and
application-default credentials (`gcloud auth application-default login`)
or a service account key on GOOGLE_APPLICATION_CREDENTIALS - activates
automatically once those exist; until then, get_queue() falls back to
LocalQueue so the rest of the system never has to special-case "no cloud
credentials yet."
"""

from __future__ import annotations

import json

from google.api_core import exceptions as gexc
from google.cloud import pubsub_v1

from app.config import GCP_PROJECT_ID
from app.queue.base import Job, JobQueue
from app.queue.local_queue import LocalQueue

# Topologies already provisioned in this process, keyed by topic path.
# Creation is idempotent but not free: each attempt is a network round trip
# that comes back AlreadyExists, and get_queue() is called on nearly every
# request. Doing it once per process turned /api/state from 7s into
# something interactive.
_provisioned: set[str] = set()

TOPIC_ID = "sentinel-investigations"
SUBSCRIPTION_ID = "sentinel-investigations-worker"


class PubSubQueue(JobQueue):
    """Job status/results still live in LocalQueue's file store (a stand-in
    for Firestore until app/store/firestore_store.py is wired to a live
    project) - only the enqueue/claim transport is real Pub/Sub, which is
    the actual "Agent Runtime (async)" requirement: a durable message queue
    a worker can be scaled independently against."""

    def __init__(self) -> None:
        if not GCP_PROJECT_ID:
            raise RuntimeError("GCP_PROJECT_ID is not set - cannot use PubSubQueue.")
        self._publisher = pubsub_v1.PublisherClient()
        self._subscriber = pubsub_v1.SubscriberClient()
        self._topic_path = self._publisher.topic_path(GCP_PROJECT_ID, TOPIC_ID)
        self._sub_path = self._subscriber.subscription_path(GCP_PROJECT_ID, SUBSCRIPTION_ID)
        self._local = LocalQueue()  # status/result bookkeeping
        self._ensure_topology()

    def _ensure_topology(self) -> None:
        """Create the topic and subscription if they are not there yet.

        Without this, a fresh project publishes into the void: enqueue()
        raises NotFound 404 and every investigation fails at submission,
        with nothing in the UI explaining why. Pub/Sub creation is
        idempotent-by-conflict rather than idempotent-by-default, so
        AlreadyExists is the success path on every run after the first.

        A PermissionDenied here is deliberately not fatal: a locked-down
        service account may legitimately be allowed to publish to a topic
        that platform tooling provisioned, but not to create one. In that
        case the resources are presumed to exist and any genuine problem
        surfaces on the publish itself.
        """
        if self._topic_path in _provisioned:
            return

        try:
            self._publisher.create_topic(request={"name": self._topic_path})
        except gexc.AlreadyExists:
            pass
        except gexc.PermissionDenied:
            _provisioned.add(self._topic_path)
            return

        try:
            self._subscriber.create_subscription(
                request={"name": self._sub_path, "topic": self._topic_path}
            )
        except (gexc.AlreadyExists, gexc.PermissionDenied):
            pass

        _provisioned.add(self._topic_path)

    def enqueue(self, job_type: str, payload: dict) -> Job:
        job = Job.new(job_type, payload)
        self._local._write(job)  # noqa: SLF001 - intentional reuse of local bookkeeping
        message = json.dumps({"job_id": job.job_id, "job_type": job_type, "payload": payload}).encode("utf-8")
        self._publisher.publish(self._topic_path, message).result()
        return job

    def claim_next(self) -> Job | None:
        response = self._subscriber.pull(request={"subscription": self._sub_path, "max_messages": 1})
        if not response.received_messages:
            return None
        msg = response.received_messages[0]
        data = json.loads(msg.message.data.decode("utf-8"))
        self._subscriber.acknowledge(request={"subscription": self._sub_path, "ack_ids": [msg.ack_id]})
        job = self._local.get(data["job_id"])
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
