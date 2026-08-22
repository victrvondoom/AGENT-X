"""
Envelope encryption for case material — the erasure pipeline's mechanism, applied
to consumer evidence.

A consumer case holds the most sensitive material a person owns: bank statements,
booking references, addresses, correspondence with companies that wronged them. It
also has to be provable months later. Those two requirements collide in exactly the
way Agent X already solved for documents:

    seal the content under a per-subject key, hash the CIPHERTEXT, and make
    erasure a matter of destroying the key rather than deleting the record.

Afterwards the case chain still verifies — the bytes that were hashed were never
touched — and the content is cryptographically unrecoverable. The user keeps the
proof that Agent X acted on their behalf and loses the personal data inside it. That
is not a compromise between the two obligations; it satisfies both.

WHERE THE KEY LIVES

On CockroachDB the key lives in the shared `subject_keys` table, so
`core.forget.forget(subject)` — the existing, unmodified erasure cascade — shreds a
case's evidence along with everything else that subject owns. One erasure, one
transaction, one certificate.

On the local engine there is no `subject_keys` table, so keys go in a portable
mirror with the same columns and the same shred semantics. The crypto is
identical (AES-256-GCM under a root key); what differs is the transactional blast
radius, and `agentx.store.describe()` says so rather than implying otherwise.
"""
from __future__ import annotations

import base64
import json
import os

from db import store as _crdb
from agentx import ids, store

TOMBSTONE = "<crypto-shredded: the key for this case was destroyed>"


class KeyDestroyed(Exception):
    """The case subject's key is gone; sealed content can never be read again."""


# ─────────────────────────────────────────────────────────────────────────────
# key storage — one table on CockroachDB, a portable mirror locally
# ─────────────────────────────────────────────────────────────────────────────
_LOCAL_DDL = """
CREATE TABLE IF NOT EXISTS agentx_subject_keys (
    workspace    TEXT NOT NULL,
    subject      TEXT NOT NULL,
    wrapped_dek  TEXT,
    created_at   TIMESTAMPTZ,
    destroyed_at TIMESTAMPTZ,
    PRIMARY KEY (workspace, subject)
)
"""


def _local_ready(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(_LOCAL_DDL)


def _root_key() -> bytes:
    """The key that wraps every per-case data key.

    `AGENT_X_ROOT_KEY` is authoritative and is the only source accepted when the
    CockroachDB engine is live — a deployment holding other people's data must
    manage its own key material, and silently minting one would mean a restart on
    a fresh host could not read anything written before it.

    On the local engine there is no operator, so a missing key would make the
    product unrunnable out of the box for the sake of a discipline nobody is
    there to keep. There, a key is minted ONCE into a file beside the local
    database and never rotated. `agentx.store.describe()` reports which of the two
    is in force, so the difference is visible rather than assumed.
    """
    raw = os.environ.get("AGENT_X_ROOT_KEY", "").strip()
    if raw:
        try:
            k = base64.b64decode(raw, validate=True)
            if len(k) in (16, 24, 32):
                return k
        except Exception:
            pass
    if store.select_engine() == "cockroachdb":
        raise RuntimeError(
            "AGENT_X_ROOT_KEY is unset or malformed. It must be a base64 "
            "AES-128/192/256 key. Generate one with "
            "`python -c \"from db import store; print(store.generate_root_key())\"` "
            "and put it in .env before running against CockroachDB.")
    return _local_root_key()


def _local_root_key() -> bytes:
    """Mint-once key material for the local engine, stored beside the database."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    path = os.path.join(os.path.dirname(store.sqlite_path()), "agentx.rootkey")
    if os.path.exists(path):
        with open(path, "rb") as f:
            key = base64.b64decode(f.read().strip())
        if len(key) in (16, 24, 32):
            return key
    key = AESGCM.generate_key(bit_length=256)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(base64.b64encode(key))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass                       # best effort; Windows ACLs are not POSIX modes
    return key


def signing_key():
    """The ECDSA P-256 key that signs receipts, packages and certificates.

    Same policy as the root key, and for the same reason: `AGENT_X_SIGNING_KEY` is
    authoritative, and where it is absent a key is minted ONCE into a file beside
    the database rather than leaving every receipt unsigned. An unsigned receipt is
    not a smaller version of a signed one — it is an artefact nobody can check, and
    shipping that by default would quietly hollow out the product's main claim.

    Minted per host and never rotated, because rotation invalidates key-pinning on
    every receipt already issued. A deployment that needs one identity across
    several hosts must set the environment variable.
    """
    import base64 as _b64
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    pem = os.environ.get("AGENT_X_SIGNING_KEY", "").strip()
    if pem:
        try:
            return serialization.load_pem_private_key(_b64.b64decode(pem), password=None)
        except Exception:
            pass

    path = os.path.join(os.path.dirname(store.sqlite_path()), "agentx.signkey")
    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                return serialization.load_pem_private_key(
                    _b64.b64decode(f.read().strip()), password=None)
        except Exception:
            pass

    key = ec.generate_private_key(ec.SECP256R1())
    blob = _b64.b64encode(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(blob)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def signing_key_source() -> str:
    if os.environ.get("AGENT_X_SIGNING_KEY", "").strip():
        return "AGENT_X_SIGNING_KEY (environment)"
    return f"local keyfile ({os.path.join(os.path.dirname(store.sqlite_path()), 'agentx.signkey')})"


def public_key_pem() -> str:
    """Agent X's public key, for pinning. Published at /api/agentx/public-key."""
    from cryptography.hazmat.primitives import serialization
    return signing_key().public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def root_key_source() -> str:
    """Which key store is in force — surfaced by `agentx.store.describe()`."""
    raw = os.environ.get("AGENT_X_ROOT_KEY", "").strip()
    if raw:
        try:
            if len(base64.b64decode(raw, validate=True)) in (16, 24, 32):
                return "AGENT_X_ROOT_KEY (environment)"
        except Exception:
            pass
    return f"local keyfile ({os.path.join(os.path.dirname(store.sqlite_path()), 'agentx.rootkey')})"


def _dek(conn, workspace: str, subject: str, *, create: bool = True) -> bytes:
    """Fetch (or mint) the plaintext data key for a case subject."""
    if store.select_engine() == "cockroachdb":
        try:
            return _crdb.get_or_create_dek(conn, workspace, subject)
        except _crdb.KeyDestroyed as e:
            raise KeyDestroyed(str(e)) from e

    _local_ready(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT wrapped_dek, destroyed_at FROM agentx_subject_keys "
                    "WHERE workspace = %s AND subject = %s", (workspace, subject))
        row = cur.fetchone()
    if row:
        wrapped, destroyed = row
        if destroyed:
            raise KeyDestroyed(subject)
        if wrapped:
            return _crdb._open(_root_key(), base64.b64decode(wrapped))
    if not create:
        raise KeyDestroyed(subject)

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    dek = AESGCM.generate_key(bit_length=256)
    wrapped_b64 = base64.b64encode(_crdb._seal(_root_key(), dek)).decode()
    with conn.cursor() as cur:
        # Guarded upsert, same race-safety reasoning as db/store.py: only a writer
        # finding a null key may fill it, and a destroyed key can never be refilled.
        cur.execute(
            "INSERT INTO agentx_subject_keys (workspace, subject, wrapped_dek, created_at) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (workspace, subject) DO UPDATE SET wrapped_dek = excluded.wrapped_dek "
            "WHERE agentx_subject_keys.wrapped_dek IS NULL "
            "  AND agentx_subject_keys.destroyed_at IS NULL",
            (workspace, subject, wrapped_b64, ids.now()))
        cur.execute("SELECT wrapped_dek, destroyed_at FROM agentx_subject_keys "
                    "WHERE workspace = %s AND subject = %s", (workspace, subject))
        row = cur.fetchone()
    if not row or row[1] or not row[0]:
        raise KeyDestroyed(subject)
    return _crdb._open(_root_key(), base64.b64decode(row[0]))


# ─────────────────────────────────────────────────────────────────────────────
# seal / unseal
# ─────────────────────────────────────────────────────────────────────────────
def seal(conn, workspace: str, subject: str, text: str) -> str:
    """Seal text under the case subject's key. Returns base64 ciphertext."""
    return base64.b64encode(_crdb._seal(_dek(conn, workspace, subject),
                                        text.encode())).decode()


def unseal(conn, workspace: str, subject: str, blob: str | None) -> str | None:
    """Open sealed content, or None if the key is gone.

    None is the honest answer after a shred, and it is not an error: a shredded
    case is a healthy case that has exercised its right to erasure. Callers render
    TOMBSTONE rather than crashing.
    """
    if not blob:
        return None
    try:
        dek = _dek(conn, workspace, subject, create=False)
    except (KeyDestroyed, Exception):
        return None
    try:
        return _crdb._open(dek, base64.b64decode(blob)).decode()
    except Exception:
        return None


def seal_json(conn, workspace: str, subject: str, obj) -> str:
    return seal(conn, workspace, subject,
                json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str))


def unseal_json(conn, workspace: str, subject: str, blob: str | None):
    raw = unseal(conn, workspace, subject, blob)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def shred(conn, workspace: str, subject: str) -> dict:
    """Destroy a case subject's key. Irreversible, and the point of the design.

    Returns a small receipt rather than nothing, because "we destroyed the key" is
    a claim a user is entitled to see evidence of — `verify_shredded` re-checks it
    from the database afterwards.
    """
    if store.select_engine() == "cockroachdb":
        _crdb.crypto_shred(conn, workspace, subject)
    else:
        _local_ready(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE agentx_subject_keys SET wrapped_dek = NULL, destroyed_at = %s "
                        "WHERE workspace = %s AND subject = %s",
                        (ids.now(), workspace, subject))
    return {"subject": subject, "workspace": workspace,
            "destroyed": True, **verify_shredded(conn, workspace, subject)}


def verify_shredded(conn, workspace: str, subject: str) -> dict:
    """Re-read the key row and report its actual state. Proof, not assertion."""
    table = "subject_keys" if store.select_engine() == "cockroachdb" else "agentx_subject_keys"
    if table == "agentx_subject_keys":
        _local_ready(conn)
    with conn.cursor() as cur:
        cur.execute(f"SELECT wrapped_dek IS NULL, destroyed_at IS NOT NULL FROM {table} "
                    "WHERE workspace = %s AND subject = %s", (workspace, subject))
        row = cur.fetchone()
    return {"key_row_present": bool(row),
            "key_material_null": bool(row and row[0]),
            "destroyed_at_set": bool(row and row[1]),
            "unrecoverable": bool(row and row[0] and row[1])}


def subject_for(case_id: str) -> str:
    """The erasure subject a case's material is sealed under.

    Per-case rather than per-user on purpose: a consumer who wants one dispute
    forgotten should not have to erase their whole history to get it, and GDPR
    Art. 17 requests in practice arrive scoped to an incident.
    """
    return f"case:{case_id}"
