"""Google Cloud backend adapters (Firestore store, Pub/Sub queue).

These ran against the real Firestore and Pub/Sub emulators during
development; the versions here mock the client libraries so CI needs no
emulator, no credentials, and no network.

The Pub/Sub tests exist because of a specific bug: the queue assumed its
topic and subscription already existed, so on a fresh project every
enqueue() raised NotFound 404 and every investigation failed at submission
with nothing in the UI explaining why.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

from google.api_core import exceptions as gexc


# --- Pub/Sub topology provisioning ---------------------------------------


def _queue_with_mocks(monkeypatch, *, create_topic_effect=None, create_sub_effect=None):
    import app.queue.pubsub_queue as pq

    monkeypatch.setattr(pq, "GCP_PROJECT_ID", "test-project")

    publisher, subscriber = MagicMock(), MagicMock()
    publisher.topic_path.return_value = "projects/test-project/topics/t"
    subscriber.subscription_path.return_value = "projects/test-project/subscriptions/s"
    if create_topic_effect:
        publisher.create_topic.side_effect = create_topic_effect
    if create_sub_effect:
        subscriber.create_subscription.side_effect = create_sub_effect

    monkeypatch.setattr(pq.pubsub_v1, "PublisherClient", lambda *a, **k: publisher)
    monkeypatch.setattr(pq.pubsub_v1, "SubscriberClient", lambda *a, **k: subscriber)
    monkeypatch.setattr(pq, "LocalQueue", MagicMock())
    return pq.PubSubQueue(), publisher, subscriber


def test_topic_and_subscription_are_created_when_missing(monkeypatch):
    """The whole point: a fresh project must not need manual provisioning."""
    _, publisher, subscriber = _queue_with_mocks(monkeypatch)
    publisher.create_topic.assert_called_once()
    subscriber.create_subscription.assert_called_once()


def test_already_existing_topology_is_not_an_error(monkeypatch):
    """Every run after the first hits AlreadyExists - that is the success
    path, not a failure, so construction must stay silent."""
    queue, _, _ = _queue_with_mocks(
        monkeypatch,
        create_topic_effect=gexc.AlreadyExists("topic"),
        create_sub_effect=gexc.AlreadyExists("sub"),
    )
    assert queue is not None


def test_permission_denied_on_create_does_not_crash_startup(monkeypatch):
    """A locked-down service account may be allowed to publish but not to
    create. That must degrade to 'assume provisioned', not refuse to boot."""
    queue, publisher, subscriber = _queue_with_mocks(
        monkeypatch, create_topic_effect=gexc.PermissionDenied("no mgmt rights")
    )
    assert queue is not None
    # Having been denied on the topic, it must not then try the subscription.
    subscriber.create_subscription.assert_not_called()


def test_publish_failures_still_propagate(monkeypatch):
    """Self-provisioning must not turn a genuinely broken publish into a
    silent success - a dropped job is worse than a loud failure."""
    queue, publisher, _ = _queue_with_mocks(monkeypatch)
    publisher.publish.side_effect = gexc.NotFound("topic vanished")
    with pytest.raises(gexc.NotFound):
        queue.enqueue("investigate_finding", {"finding_id": "F-1"})


def test_project_id_is_required(monkeypatch):
    import app.queue.pubsub_queue as pq

    monkeypatch.setattr(pq, "GCP_PROJECT_ID", None)
    with pytest.raises(RuntimeError, match="GCP_PROJECT_ID"):
        pq.PubSubQueue()


# --- Firestore store ------------------------------------------------------


def test_firestore_store_requires_a_project(monkeypatch):
    import app.store.firestore_store as fs

    monkeypatch.setattr(fs, "GCP_PROJECT_ID", None)
    with pytest.raises(RuntimeError, match="GCP_PROJECT_ID"):
        fs.FirestoreStore()


def test_missing_document_reads_back_as_none(monkeypatch):
    """Verified against the real emulator: a get on an absent finding must
    be None, not an empty dict, or 'no evidence yet' renders as sealed."""
    import app.store.firestore_store as fs

    monkeypatch.setattr(fs, "GCP_PROJECT_ID", "test-project")
    client = MagicMock()
    doc = MagicMock()
    doc.exists = False
    client.collection.return_value.document.return_value.get.return_value = doc
    monkeypatch.setattr(fs.firestore, "Client", lambda **k: client)

    assert fs.FirestoreStore().get_evidence("nope") is None


def test_evidence_round_trips_through_the_document_api(monkeypatch):
    import app.store.firestore_store as fs

    monkeypatch.setattr(fs, "GCP_PROJECT_ID", "test-project")
    client = MagicMock()
    record = {"finding_id": "F-1", "signature": "sha256:abc", "nested": {"a": [1, 2]}}
    doc = MagicMock()
    doc.exists = True
    doc.to_dict.return_value = record
    client.collection.return_value.document.return_value.get.return_value = doc
    monkeypatch.setattr(fs.firestore, "Client", lambda **k: client)

    store = fs.FirestoreStore()
    store.put_evidence("F-1", record)
    client.collection.return_value.document.return_value.set.assert_called_once_with(record)
    assert store.get_evidence("F-1") == record
