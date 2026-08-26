"""
The portable attestation — Pattern 4, and the thing a judge actually checks.

A certificate binds three facts together so that verifying one verifies all of them:

    the AUDIT HEAD      the hash of the last chain entry, plus the chain LENGTH
    the ARTEFACT        the sha256 of the signed document
    the DECISIONS       every field, how it was routed, and who ruled on it

Carrying `chain_length` next to `chain_head` is what closes the truncation hole
documented in Phase 1: a chain with rows lopped off the tail is still internally
valid, but it no longer has the recorded length OR the recorded head, so the
certificate catches what hash-chaining alone cannot.

Signatures are emitted in BOTH encodings on purpose. Python's `cryptography` returns
ECDSA in DER; WebCrypto in a browser wants raw r||s (P1363). Shipping only DER would
mean the offline verifier has to parse ASN.1 before it can check anything, and a
verifier that is hard to run is a verifier nobody runs.
"""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from . import audit

SPEC_VERSION = "trustdoc-certificate/v1"


def canonical(obj: Any) -> bytes:
    """Byte-for-byte reproducible JSON. Any verifier must hash exactly these bytes."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str).encode("utf-8")


def _der_to_raw(der: bytes) -> bytes:
    """Convert a DER ECDSA signature to raw r||s, 32 bytes each (P-256).

    Minimal ASN.1 walk rather than a dependency: SEQUENCE { INTEGER r, INTEGER s }.
    DER integers are signed, so a leading 0x00 is stripped and short values are
    left-padded — getting either wrong yields a signature that silently fails to
    verify in the browser while looking perfectly fine in Python.
    """
    if not der or der[0] != 0x30:
        raise ValueError("not a DER SEQUENCE")
    i = 2 if der[1] < 0x80 else 2 + (der[1] & 0x7F)

    def read_int(p: int) -> tuple[bytes, int]:
        if der[p] != 0x02:
            raise ValueError("expected DER INTEGER")
        ln = der[p + 1]
        v = der[p + 2: p + 2 + ln].lstrip(b"\x00")
        return v.rjust(32, b"\x00"), p + 2 + ln

    r, i = read_int(i)
    s, _ = read_int(i)
    return r + s


def build(conn, job_id: str, *, kind: str, document_sha256: str | None,
          fields: list[dict], extra: dict | None = None) -> dict:
    """Assemble the certificate body. Unsigned — sign() adds the attestation."""
    v = audit.verify_chain(conn, job_id)
    ch = audit.chain(conn, job_id)

    with conn.cursor() as cur:
        cur.execute("SELECT kind, doc_type, status, created_at::text FROM jobs WHERE id = %s",
                    (job_id,))
        row = cur.fetchone()
    if row is None:
        raise ValueError(f"no such job {job_id}")

    return {
        "spec": SPEC_VERSION,
        "job_id": job_id,
        "pipeline": kind,
        "doc_type": row[1],
        "status": row[2],
        "created_at": row[3],
        # the audit binding -- head AND length, so truncation is detectable
        "chain_head": v.get("head"),
        "chain_length": v.get("rows"),
        "chain_intact_at_issue": v.get("ok"),
        "document_sha256": document_sha256,
        "fields": [
            {"name": f["name"], "value": f.get("value"),
             "decision": f["decision"], "reason": f.get("reason"),
             "confidence": f.get("confidence"),
             "reviewed_by": f.get("reviewed_by"), "reviewed_at": f.get("reviewed_at")}
            for f in sorted(fields, key=lambda x: x["name"])
        ],
        "counts": {
            "total": len(fields),
            "auto": sum(1 for f in fields if f["decision"] == "AUTO"),
            "human": sum(1 for f in fields if f["decision"] == "HUMAN"),
            "corrected": sum(1 for f in fields if f.get("reviewed_by")
                             and f.get("value") != f.get("original_value")),
        },
        "audit_steps": [{"seq": e["seq"], "step": e["step"], "actor": e["actor"],
                         "content_hash": e["content_hash"]} for e in ch],
        **(extra or {}),
    }


def sign(cert: dict, private_key=None) -> dict:
    """Hash and sign a certificate body. Returns the portable envelope."""
    body = canonical(cert)
    sha = hashlib.sha256(body).hexdigest()
    env: dict[str, Any] = {"certificate": cert, "sha256": sha, "signed": False}

    if private_key is None:
        env["reason"] = "no signing key configured; certificate is hashed but unsigned"
        return env

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    der = private_key.sign(body, ec.ECDSA(hashes.SHA256()))
    env.update({
        "signed": True,
        "algorithm": "ECDSA-P256-SHA256",
        "signature": base64.b64encode(der).decode(),          # DER, for openssl/python
        "signature_raw": base64.b64encode(_der_to_raw(der)).decode(),  # r||s, for WebCrypto
        "public_key": private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo).decode(),
        "verify_hint": "sha256 is over canonical JSON of `certificate`: "
                       "sorted keys, separators (',',':'), UTF-8, no whitespace.",
    })
    return env


def verify(env: dict, conn=None, trusted_public_key: str | None = None) -> dict:
    """Verify a certificate. Three independent checks, reported separately.

    Separately, because they fail for different reasons and a judge deserves to know
    WHICH one failed: a bad hash means the body was edited, a bad signature means it
    was not issued by this key, and a chain mismatch means the database no longer
    agrees with the attestation.

    `conn` is optional on purpose. Hash and signature verify with NOTHING but the
    certificate and the public key inside it — no database, no server, no us.

    IMPORTANT LIMITATION, and the reason `trusted_public_key` exists. A certificate
    carries the public key its signature is checked against, so ANYONE can mint one
    that is internally valid: edit a value, re-sign with their own key, embed their
    own key. Hash and signature both pass. Nothing self-contained can detect that —
    it is the digital equivalent of a forged document with a forged letterhead.

    Two things catch it, and a verifier must do at least one:
      * pin the key: pass `trusted_public_key` (published separately) and any
        certificate signed by another key is rejected as UNTRUSTED_KEY;
      * check the binding: a forgery is not in the database, so the live audit chain
        will not match its attested head and length.
    """
    out: dict[str, Any] = {"checks": {}, "ok": False}
    cert = env.get("certificate")
    if not isinstance(cert, dict):
        out["error"] = "envelope has no `certificate` object"
        return out

    # 1. content hash
    recomputed = hashlib.sha256(canonical(cert)).hexdigest()
    claimed = env.get("sha256")
    out["recomputed_sha256"] = recomputed
    out["claimed_sha256"] = claimed
    out["checks"]["content_hash"] = {
        "ok": claimed is not None and recomputed == claimed,
        "detail": ("matches" if claimed == recomputed else
                   "MISMATCH -- the certificate body was altered after signing"),
    }

    # 2. signature, using only the embedded public key
    sig, pub = env.get("signature"), env.get("public_key")
    if trusted_public_key and pub:
        # Compare the key material, not the PEM text: whitespace and line wrapping
        # differ harmlessly between tools, and a string compare would reject a
        # legitimate certificate for cosmetic reasons.
        norm = lambda p: "".join(p.split()).replace(
            "-----BEGINPUBLICKEY-----", "").replace("-----ENDPUBLICKEY-----", "")
        if norm(pub) != norm(trusted_public_key):
            out["checks"]["trusted_key"] = {
                "ok": False,
                "detail": "UNTRUSTED KEY -- this certificate is signed by a key that is "
                          "not the published one. It may be internally valid and still "
                          "be a forgery."}
        else:
            out["checks"]["trusted_key"] = {
                "ok": True, "detail": "signed by the published key"}
    if not sig or not pub:
        out["checks"]["signature"] = {"ok": None, "detail": "certificate is unsigned"}
    else:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        try:
            key = serialization.load_pem_public_key(pub.encode())
            if not isinstance(key, ec.EllipticCurvePublicKey):
                raise TypeError(f"expected an EC public key, got {type(key).__name__}")
            key.verify(base64.b64decode(sig), canonical(cert), ec.ECDSA(hashes.SHA256()))
            out["checks"]["signature"] = {"ok": True, "detail": "valid ECDSA P-256"}
        except InvalidSignature:
            out["checks"]["signature"] = {
                "ok": False,
                "detail": "INVALID -- not signed by the embedded public key"}
        except Exception as e:
            out["checks"]["signature"] = {"ok": False, "detail": f"unverifiable: {e}"}

    # 3. the live chain still matches what was attested
    if conn is None:
        out["checks"]["audit_chain"] = {
            "ok": None, "detail": "not checked (offline verification)"}
    else:
        live = audit.verify_chain(conn, cert["job_id"])
        same_head = live.get("head") == cert.get("chain_head")
        same_len = live.get("rows") == cert.get("chain_length")
        ok = bool(live.get("ok") and same_head and same_len)
        if ok:
            detail = f"intact, {live['rows']} rows, head matches the certificate"
        elif not live.get("ok"):
            detail = f"BROKEN -- {live.get('reason')}"
        elif not same_len:
            detail = (f"LENGTH MISMATCH -- certificate attests {cert.get('chain_length')} "
                      f"rows, database has {live.get('rows')} (entries were added or removed)")
        else:
            detail = ("HEAD MISMATCH -- the chain no longer ends where the certificate "
                      "says it did")
        out["checks"]["audit_chain"] = {"ok": ok, "detail": detail}

    decided = [c["ok"] for c in out["checks"].values() if c["ok"] is not None]
    out["ok"] = bool(decided) and all(decided)
    return out
