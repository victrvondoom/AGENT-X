"""
Erasure certificates — the tamper-evident proof-of-erasure.

For each erasure we build a compact certificate that contains NO personal data (the subject is
hashed), sign it with ECDSA, and — when AWS is configured — write it to an S3 bucket under
Object Lock (WORM / compliance mode) so the receipt itself cannot be altered or deleted.

Without AWS configured the certificate is still built and signed locally, so the proof works in
development; S3 storage activates automatically once credentials are present.
"""
from __future__ import annotations

import os
import json
import base64
import hashlib
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))


def aws_configured() -> bool:
    return bool(os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("S3_CERT_BUCKET"))


def _signing_key():
    pem = os.environ.get("AGENT_X_SIGNING_KEY")
    if not pem:
        return None
    return serialization.load_pem_private_key(base64.b64decode(pem), password=None)


def generate_signing_key() -> str:
    """Return a base64 PEM ECDSA private key (store in AGENT_X_SIGNING_KEY)."""
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return base64.b64encode(pem).decode()


def public_key_pem() -> str | None:
    key = _signing_key()
    if not key:
        return None
    return key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()


def verify(payload: dict) -> dict:
    """Independently verify an erasure certificate: re-derive its SHA-256 content hash and check
    the ECDSA signature against Agent X's public key. Anyone can run this on the portable cert —
    a tampered field changes the hash; a forged cert fails the signature check."""
    cert = payload.get("certificate") if isinstance(payload.get("certificate"), dict) else payload
    body = json.dumps(cert, sort_keys=True, separators=(",", ":")).encode()
    recomputed = hashlib.sha256(body).hexdigest()
    claimed = payload.get("sha256")
    hash_matches = None if claimed is None else (recomputed == claimed)

    signature_valid = None
    sig = payload.get("signature")
    key = _signing_key()
    if sig and key:
        from cryptography.exceptions import InvalidSignature
        try:
            key.public_key().verify(base64.b64decode(sig), body, ec.ECDSA(hashes.SHA256()))
            signature_valid = True
        except InvalidSignature:
            signature_valid = False
        except Exception:
            signature_valid = None

    return {
        "content_hash": recomputed,
        "claimed_hash": claimed,
        "hash_matches": hash_matches,
        "signature_valid": signature_valid,
        "subject_sha256": cert.get("subject_sha256"),
        "event_id": cert.get("event_id"),
        "issued_at": cert.get("issued_at"),
        "guarantees": cert.get("guarantees"),
        "public_key_pem": public_key_pem(),
    }


def issue(receipt: dict, proof_prior: list, proof_absence: dict, issued_at: str) -> dict:
    """Build → sign → (optionally) store an erasure certificate. Returns cert metadata.

    Claims are precise about what erasure guarantees: the subject's source DOCUMENTS are
    encrypted and their key destroyed (content cryptographically unrecoverable); exclusive graph
    entities + edges are deleted; entities shared with surviving subjects are retained for them
    with this subject's provenance removed. We do NOT claim the derived graph text is crypto-shredded.
    """
    workspace = receipt.get("workspace", "default")
    # Salt the subject hash with the random per-event salt (kept in erasure_events, NOT in this
    # certificate) so the portable cert cannot be brute-forced back to the subject.
    salt = bytes.fromhex(receipt["salt"]) if receipt.get("salt") else b""
    subject_sha = hashlib.sha256(salt + f"{workspace}:{receipt['subject']}".encode()).hexdigest()
    shredded = bool(proof_absence.get("key_shredded"))
    cert = {
        "agent_x_erasure_certificate": "v1",
        "subject_sha256": subject_sha,
        "event_id": receipt["event_id"],
        "t_before": receipt["t_before"],
        "removed": {
            "documents": receipt["docs"],
            "exclusive_nodes": receipt["nodes"],
            "exclusive_edges": receipt["edges"],
            "shared_nodes_retained": receipt["invalidated"],
        },
        "prior_existence_entities": len(proof_prior),
        "guarantees": {
            "document_content_crypto_shredded": shredded,
            "exclusive_graph_deleted": True,
            "subject_provenance_removed_from_shared_nodes": True,
        },
        "issued_at": issued_at,
    }
    body = json.dumps(cert, sort_keys=True, separators=(",", ":")).encode()
    result = {"certificate": cert, "sha256": hashlib.sha256(body).hexdigest(), "stored": False}

    key = _signing_key()
    if key:
        sig = key.sign(body, ec.ECDSA(hashes.SHA256()))
        result["signature"] = base64.b64encode(sig).decode()

    if aws_configured():
        try:
            import boto3

            bucket = os.environ["S3_CERT_BUCKET"]
            s3_key = f"certificates/{subject_sha[:16]}-{receipt['event_id']}.json"
            s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "ap-south-1"))
            # WORM lock, configurable so demo buckets aren't locked for a decade.
            #   S3_LOCK_MODE: COMPLIANCE (no one can delete before expiry) | GOVERNANCE (privileged override)
            #   S3_LOCK_DAYS: retention window (default 1 day — enough to demo WORM, short enough to clean up)
            lock_mode = os.environ.get("S3_LOCK_MODE", "COMPLIANCE").upper()
            lock_days = int(os.environ.get("S3_LOCK_DAYS", "1"))
            s3.put_object(
                Bucket=bucket,
                Key=s3_key,
                Body=body,
                ContentType="application/json",
                ObjectLockMode=lock_mode,
                ObjectLockRetainUntilDate=datetime.now(timezone.utc) + timedelta(days=lock_days),
                Metadata={"sha256": result["sha256"]},
            )
            result.update(stored=True, s3_bucket=bucket, s3_key=s3_key)
        except Exception as e:  # never let cert storage break the erasure
            result["store_error"] = str(e)[:200]

    return result
