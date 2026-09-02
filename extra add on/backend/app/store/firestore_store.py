"""Real Firestore-backed evidence store. Activates once GCP_PROJECT_ID and
credentials exist (`gcloud auth application-default login`) and
SENTINEL_STORE_BACKEND=firestore is set.
"""

from __future__ import annotations

from google.cloud import firestore

from app.config import GCP_PROJECT_ID
from app.store.base import EvidenceStore

COLLECTION = "sentinel_evidence"


class FirestoreStore(EvidenceStore):
    def __init__(self) -> None:
        if not GCP_PROJECT_ID:
            raise RuntimeError("GCP_PROJECT_ID is not set - cannot use FirestoreStore.")
        self._client = firestore.Client(project=GCP_PROJECT_ID)

    def put_evidence(self, finding_id: str, evidence: dict) -> None:
        self._client.collection(COLLECTION).document(finding_id).set(evidence)

    def get_evidence(self, finding_id: str) -> dict | None:
        doc = self._client.collection(COLLECTION).document(finding_id).get()
        return doc.to_dict() if doc.exists else None

    def list_evidence(self) -> list[dict]:
        return [doc.to_dict() for doc in self._client.collection(COLLECTION).stream()]
