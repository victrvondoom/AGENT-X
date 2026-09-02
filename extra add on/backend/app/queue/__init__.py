"""Async job queue abstraction - the mechanism behind "close your laptop,
come back later" (Google's Agent Runtime / async-operations requirement).

One interface, three backends:
- LocalQueue: file-based, works today with zero cloud credentials - real
  async execution via a background worker process, not a synchronous call
  pretending to be async.
- PubSubQueue: real google-cloud-pubsub client, activates once GCP_PROJECT_ID
  and credentials are present.
- EventBridgeQueue: real boto3 client, activates once AWS credentials are
  present.

get_queue() picks the backend from environment config, so investigation
code (worker.py) never needs to know which one is active.
"""

from __future__ import annotations

import os
import threading

from app.queue.base import Job, JobQueue
from app.queue.local_queue import LocalQueue


# Cached per backend. The cloud queues hold gRPC channels that are
# expensive to build and are designed to be long-lived; get_queue() is
# called on nearly every request, so constructing a fresh client each time
# meant a new connection (and, for Pub/Sub, a topology check) per call.
_lock = threading.Lock()
_instances: dict[str, JobQueue] = {}


def get_queue() -> JobQueue:
    backend = os.environ.get("SENTINEL_QUEUE_BACKEND", "local").lower()
    with _lock:
        cached = _instances.get(backend)
        if cached is not None:
            return cached

        if backend == "pubsub":
            from app.queue.pubsub_queue import PubSubQueue

            queue: JobQueue = PubSubQueue()
        elif backend == "eventbridge":
            from app.queue.eventbridge_queue import EventBridgeQueue

            queue = EventBridgeQueue()
        else:
            queue = LocalQueue()

        _instances[backend] = queue
        return queue


def reset_queue_cache() -> None:
    """Drop cached clients. For tests, and for any code that changes the
    backend environment variable at runtime."""
    with _lock:
        _instances.clear()


__all__ = ["Job", "JobQueue", "get_queue", "reset_queue_cache"]
