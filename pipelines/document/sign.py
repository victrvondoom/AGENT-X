"""
Signing — and a distinction the README must not blur.

There are TWO different signatures in this product, and calling both "signed"
would overstate what is guaranteed:

  1. DOCUMENT signature (POST /sign) — DWS embeds a signature INSIDE the PDF, so a
     PDF reader shows it as signed. Requires DWS_API_KEY. Nutrient's public docs do
     not state whether ECDSA P-256 is offered, so that must be confirmed against a
     real account before the README claims it.

  2. DETACHED signature (here) — our own ECDSA P-256 over the document's SHA-256.
     It proves THIS pipeline produced THIS artefact, and it is what the Phase 5
     certificate carries. It does NOT make the PDF itself a signed PDF.

Without a DWS key only (2) is available, and the audit trail records exactly which
one happened. A judge reading "signed" is entitled to know which of these it means.
"""
from __future__ import annotations

import base64
import os

from . import dws


class SigningUnavailable(RuntimeError):
    pass


# ── 1. document signature, via DWS ────────────────────────────────────────
def sign_document(pdf: bytes, filename: str = "document.pdf") -> bytes:
    """Embed a signature in the PDF via DWS. Raises if DWS is not configured."""
    if not dws.configured():
        raise dws.DWSUnavailable(
            "DWS_API_KEY is not set, so POST /sign cannot embed a document "
            "signature. A detached certificate signature is still produced, but the "
            "PDF itself will not be a signed PDF."
        )
    import httpx
    with httpx.Client(base_url=dws.BASE_URL, timeout=120.0,
                      headers={"Authorization": f"Bearer {dws.api_key()}"}) as c:
        r = c.post(dws.SIGN_PATH, files={"file": (filename, pdf)})
    if r.status_code >= 400:
        raise dws.DWSError(f"POST {dws.SIGN_PATH} -> {r.status_code}: {r.text[:400]}")
    return r.content


# ── 2. detached signature, ours ───────────────────────────────────────────
def _key():
    """Reuse the erasure pipeline's signing key.

    One product, one signing identity: a verifier that trusts our public key for an
    erasure certificate should not need a second key for a compliance certificate.
    """
    from cryptography.hazmat.primitives import serialization
    pem = os.environ.get("AGENT_X_SIGNING_KEY")
    if not pem:
        return None
    return serialization.load_pem_private_key(base64.b64decode(pem), password=None)


def generate_signing_key() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    k = ec.generate_private_key(ec.SECP256R1())
    return base64.b64encode(k.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption())).decode()


def public_key_pem() -> str | None:
    from cryptography.hazmat.primitives import serialization
    k = _key()
    if not k:
        return None
    return k.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def sign_detached(payload: bytes) -> dict:
    """ECDSA P-256 over `payload`. Returns algorithm + signature + public key.

    Reports algorithm explicitly so a verifier never has to guess, and returns
    signed=False rather than raising when no key is configured — an unsigned
    artefact that says it is unsigned is safe; one that pretends is not.
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    k = _key()
    if not k:
        return {"signed": False, "algorithm": None, "signature": None,
                "public_key": None,
                "reason": "AGENT_X_SIGNING_KEY is not set; artefact is unsigned"}
    sig = k.sign(payload, ec.ECDSA(hashes.SHA256()))
    return {"signed": True, "algorithm": "ECDSA-P256-SHA256",
            "signature": base64.b64encode(sig).decode(),
            "public_key": public_key_pem()}


def verify_detached(payload: bytes, signature_b64: str, public_pem: str) -> bool:
    """Verify a detached signature with only the public key — no server needed."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    pub = serialization.load_pem_public_key(public_pem.encode())
    try:
        pub.verify(base64.b64decode(signature_b64), payload, ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        return False
