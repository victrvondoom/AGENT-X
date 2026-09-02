"""Real DynamoDB-backed evidence store. Activates once AWS credentials
exist and SENTINEL_STORE_BACKEND=dynamodb / SENTINEL_DYNAMODB_TABLE are set.
"""

from __future__ import annotations

import os

import boto3

from agentx.subsystems.sentinel_x.store.base import EvidenceStore

TABLE_NAME = os.environ.get("SENTINEL_DYNAMODB_TABLE", "sentinel_evidence")


class DynamoDBStore(EvidenceStore):
    def __init__(self) -> None:
        self._table = boto3.resource("dynamodb").Table(TABLE_NAME)

    def put_evidence(self, finding_id: str, evidence: dict) -> None:
        self._table.put_item(Item={"finding_id": finding_id, **evidence})

    def get_evidence(self, finding_id: str) -> dict | None:
        response = self._table.get_item(Key={"finding_id": finding_id})
        return response.get("Item")

    def list_evidence(self) -> list[dict]:
        return self._table.scan().get("Items", [])
