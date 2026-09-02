"""Evidence/state store abstraction - one interface satisfying both Google's
Firestore requirement and AWS's DynamoDB requirement. LocalJsonStore (what
Evidence Agent already writes to in app/agents/evidence_agent.py) is the
default; FirestoreStore/DynamoDBStore are real client implementations that
activate once the relevant cloud credentials exist.
"""

from __future__ import annotations

import os
import threading

from agentx.subsystems.sentinel_x.store.base import EvidenceStore
from agentx.subsystems.sentinel_x.store.local_store import LocalJsonStore


# Cached per backend, for the same reason as the queue: a Firestore client
# opens a gRPC channel that is meant to be long-lived and is safe to share
# across threads, while get_store() is called on nearly every request.
_lock = threading.Lock()
_instances: dict[str, EvidenceStore] = {}


def get_store() -> EvidenceStore:
    backend = os.environ.get("SENTINEL_STORE_BACKEND", "local").lower()
    with _lock:
        cached = _instances.get(backend)
        if cached is not None:
            return cached

        if backend == "firestore":
            from agentx.subsystems.sentinel_x.store.firestore_store import FirestoreStore

            store: EvidenceStore = FirestoreStore()
        elif backend == "dynamodb":
            from agentx.subsystems.sentinel_x.store.dynamodb_store import DynamoDBStore

            store = DynamoDBStore()
        else:
            store = LocalJsonStore()

        _instances[backend] = store
        return store


def reset_store_cache() -> None:
    """Drop cached clients. For tests, and for any code that changes the
    backend environment variable at runtime."""
    with _lock:
        _instances.clear()


__all__ = ["EvidenceStore", "get_store", "reset_store_cache"]
