"""
Nutrient DWS client — the real API, or an honest failure.

Endpoints and auth here were read from Nutrient's live documentation (see
docs/DWS_API.md), not inferred. The one that matters:

    POST /extract   maps a document against a JSON Schema you supply and returns
                    the requested fields with per-field confidence and citations.

NOT /build. `/build` is the Processor composite endpoint — it returns a DOCUMENT,
not typed fields, so a pipeline built against it would never see a confidence score
at all. That distinction is the whole reason Step 0 existed.

This module never invents a response. With no API key configured it raises
DWSUnavailable naming what is missing; it does not quietly return plausible data.
An extraction pipeline that fabricates confidence scores is worse than one that
stops, because the fabrication travels downstream into a signed certificate.
"""
from __future__ import annotations

import os
from typing import Any

BASE_URL = os.environ.get("DWS_BASE_URL", "https://api.nutrient.io")
EXTRACT_PATH = "/extract"
SIGN_PATH = "/sign"
TOKENS_PATH = "/tokens"


class DWSUnavailable(RuntimeError):
    """Raised when DWS cannot be called — missing key, bad key, or transport failure.

    Deliberately distinct from a DWS *rejection*: "we could not ask" and "DWS said
    no" are different facts and the audit trail must not conflate them.
    """


class DWSError(RuntimeError):
    """DWS was reached and returned an error response."""


def api_key() -> str:
    key = os.environ.get("DWS_API_KEY", "").strip()
    if not key:
        raise DWSUnavailable(
            "DWS_API_KEY is not set. Put your Nutrient DWS key in .env "
            "(DWS_API_KEY=...) — it is gitignored. Without it the extraction step "
            "cannot run; nothing here will substitute a synthetic response."
        )
    return key


def configured() -> bool:
    """True when a live call is possible. Lets callers degrade honestly."""
    return bool(os.environ.get("DWS_API_KEY", "").strip())


def _client(timeout: float = 120.0):
    try:
        import httpx
    except ImportError as e:                                   # pragma: no cover
        raise DWSUnavailable(f"httpx is not installed: {e}")
    return httpx.Client(
        base_url=BASE_URL,
        timeout=timeout,
        headers={"Authorization": f"Bearer {api_key()}"},
    )


def session_token(allowed_operations: list[str], allowed_origins: list[str],
                  expires_in: int = 3600) -> dict:
    """Mint a scoped, short-lived JWT for the browser (the Viewer, in Phase 3).

    The raw API key must never reach the client, so the Viewer is authorised with
    one of these instead.
    """
    with _client(timeout=30.0) as c:
        r = c.post(TOKENS_PATH, json={
            "allowedOperations": allowed_operations,
            "allowedOrigins": allowed_origins,
            "expirationTime": expires_in,
        })
    if r.status_code >= 400:
        raise DWSError(f"POST {TOKENS_PATH} -> {r.status_code}: {r.text[:400]}")
    return r.json()


def extract(file_bytes: bytes, filename: str, schema: dict[str, Any]) -> dict:
    """Run field extraction against a JSON Schema. Returns the raw DWS response.

    Kept deliberately thin — parsing lives in normalise() so the untouched response
    can be recorded in the audit trail. If we ever disagree with DWS about what a
    field was, the original answer is on the record.
    """
    import json as _json
    with _client() as c:
        r = c.post(
            EXTRACT_PATH,
            files={"file": (filename, file_bytes)},
            data={"schema": _json.dumps(schema)},
        )
    if r.status_code >= 400:
        raise DWSError(f"POST {EXTRACT_PATH} -> {r.status_code}: {r.text[:600]}")
    return r.json()


def normalise(response: dict) -> list[dict]:
    """Flatten a DWS extraction response into the field rows this product stores.

    DWS nests differently depending on whether citations are enabled and whether a
    value is scalar or a table cell, so several shapes are accepted. Anything whose
    confidence cannot be located comes back as None — NOT 0.0. That distinction is
    load-bearing: the gate routes absent confidence to a human on purpose, and
    coercing it to zero here would destroy the difference before the gate ever
    sees it.
    """
    out: list[dict] = []

    def bbox_of(meta: dict) -> dict | None:
        for k in ("source_bboxes", "bounds", "bbox", "boundingBox"):
            v = meta.get(k)
            if isinstance(v, list) and v:
                return v[0] if isinstance(v[0], dict) else None
            if isinstance(v, dict):
                return v
        return None

    def conf_of(meta: dict) -> float | None:
        v = meta.get("confidence", meta.get("relationshipConfidence"))
        return float(v) if isinstance(v, (int, float)) else None

    def walk(node: Any, path: str = "") -> None:
        if isinstance(node, dict):
            # a leaf carrying a value plus citation metadata
            if "value" in node and not isinstance(node.get("value"), (dict, list)):
                rec = node.get("recognitionScore")
                out.append({
                    "name": path or node.get("name") or "field",
                    "value": None if node["value"] is None else str(node["value"]),
                    "confidence": conf_of(node),
                    "recognition": float(rec) if isinstance(rec, (int, float)) else None,
                    "source_bbox": bbox_of(node),
                })
                return
            for k, v in node.items():
                if k in ("confidence", "confidenceComponents", "recognitionScore",
                         "source_blocks", "source_bboxes", "bounds", "bbox"):
                    continue
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(response.get("data", response.get("fields", response)))
    return out
