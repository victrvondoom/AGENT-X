"""Shared test fixtures.

The advisory cache is disk-backed and process-global, so without isolation
one test's resolved lookup leaks into the next test's "the network is down"
scenario and silently turns a fail-closed assertion green. Clearing it
around every test keeps each one honest about what it actually exercises.
"""

from __future__ import annotations

import pytest

from app.knowledge import advisory_cache


@pytest.fixture(autouse=True)
def _isolate_advisory_cache():
    advisory_cache.clear()
    yield
    advisory_cache.clear()


@pytest.fixture(autouse=True)
def _isolate_process_caches():
    """Clear the per-process client caches and the Pub/Sub provisioning
    guard between tests.

    All three are correct in production - they exist so a gRPC channel and a
    topology check are paid for once rather than on every request - but they
    are process-global, so without this one test's construction satisfies
    the next test's assertion. It was already happening: the "topic is
    created when missing" test passed only because it ran first in its file,
    and failed the moment any other test constructed a queue before it.
    """
    from app.queue import reset_queue_cache
    from app.queue import pubsub_queue
    from app.store import reset_store_cache

    def _clear() -> None:
        reset_queue_cache()
        reset_store_cache()
        pubsub_queue._provisioned.clear()

    _clear()
    yield
    _clear()
