"""
Agent X storage layer — CockroachDB.

One transactional store holds documents, the knowledge graph (nodes + edges), vector
embeddings, per-subject encryption keys, and the erasure audit trail. This module owns:

  * a pooled CockroachDB connection,
  * envelope encryption (per-subject data keys wrapped by a root key), and
  * crypto-shredding — destroying a subject's key so residual ciphertext (in MVCC history,
    backups, or S3) is cryptographically unrecoverable.

Design note: content is *never* stored in plaintext. Each subject has a data-encryption key
(DEK); document text is sealed under that DEK. Erasure destroys the wrapped DEK, which is the
load-bearing guarantee behind "provably forgotten" — deletion alone leaves recoverable bytes
(cf. "Ghost Vectors", arXiv:2606.18497); key destruction does not.
"""
from __future__ import annotations

import os
import atexit
import base64
from contextlib import contextmanager

from psycopg_pool import ConnectionPool
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. Copy .env.example to .env, set your CockroachDB connection "
        "string, then run: python scripts/init_db.py"
    )

_pool: ConnectionPool | None = None


def pool() -> ConnectionPool:
    """Process-wide connection pool (one TLS handshake amortized across requests)."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            DATABASE_URL,
            min_size=1,
            max_size=8,
            kwargs={"autocommit": True},
            open=True,
        )
    return _pool


@contextmanager
def connect():
    """Borrow a pooled connection."""
    with pool().connection() as conn:
        yield conn


# ─────────────────────────────────────────────────────────────────────────────
# Envelope encryption / crypto-shred
# ─────────────────────────────────────────────────────────────────────────────
class KeyDestroyed(Exception):
    """Raised when a subject's data key has been crypto-shredded (subject was erased)."""


def generate_root_key() -> str:
    """Return a fresh base64-encoded 256-bit root key (store in AGENT_X_ROOT_KEY)."""
    return base64.b64encode(AESGCM.generate_key(bit_length=256)).decode()


def _root_key() -> bytes:
    k = os.environ.get("AGENT_X_ROOT_KEY")
    if not k:
        raise RuntimeError(
            "AGENT_X_ROOT_KEY is not set. Generate one with store.generate_root_key()."
        )
    return base64.b64decode(k)


def _seal(key: bytes, plaintext: bytes) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, None)


def _open(key: bytes, blob: bytes) -> bytes:
    blob = bytes(blob)
    return AESGCM(key).decrypt(blob[:12], blob[12:], None)


def get_or_create_dek(conn, workspace: str, subject: str) -> bytes:
    """Return the plaintext DEK for a (workspace, subject), minting + wrapping one on first use.

    Raises KeyDestroyed if the subject was already erased (shredded key).
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT wrapped_dek, destroyed_at FROM subject_keys WHERE workspace = %s AND subject = %s",
            (workspace, subject),
        )
        row = cur.fetchone()
        if row:
            wrapped, destroyed = row
            if destroyed is not None or wrapped is None:
                raise KeyDestroyed(subject)
            return _open(_root_key(), wrapped)
        # Mint a key but only persist it if none exists yet (ON CONFLICT DO NOTHING), then re-read
        # the WINNING key — so two concurrent first-ingests converge on one key instead of one of
        # them encrypting a document under a key that gets overwritten (and thus lost forever).
        dek = AESGCM.generate_key(bit_length=256)
        cur.execute(
            "INSERT INTO subject_keys (workspace, subject, wrapped_dek) VALUES (%s, %s, %s) "
            "ON CONFLICT (workspace, subject) DO NOTHING",
            (workspace, subject, _seal(_root_key(), dek)),
        )
        cur.execute(
            "SELECT wrapped_dek, destroyed_at FROM subject_keys WHERE workspace = %s AND subject = %s",
            (workspace, subject),
        )
        row = cur.fetchone()
        if row is None:          # key row vanished between INSERT and re-read (concurrent erase)
            raise KeyDestroyed(subject)
        wrapped, destroyed = row
        if destroyed is not None or wrapped is None:
            raise KeyDestroyed(subject)
        return _open(_root_key(), wrapped)


def encrypt_for(conn, workspace: str, subject: str, text: str) -> bytes:
    """Seal `text` under the (workspace, subject) data key."""
    return _seal(get_or_create_dek(conn, workspace, subject), text.encode())


def decrypt_for(conn, workspace: str, subject: str, blob) -> str | None:
    """Open sealed content. Returns None if the key is gone (erased) — proving that even
    retained ciphertext is unrecoverable after a crypto-shred."""
    if blob is None:
        return None
    with conn.cursor() as cur:
        cur.execute(
            "SELECT wrapped_dek FROM subject_keys WHERE workspace = %s AND subject = %s",
            (workspace, subject),
        )
        row = cur.fetchone()
    if not row or row[0] is None:
        return None
    return _open(_open(_root_key(), row[0]), blob).decode()


def crypto_shred(conn, workspace: str, subject: str) -> None:
    """Destroy a (workspace, subject) data key. Irreversible: sealed content can never be opened again."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE subject_keys SET wrapped_dek = NULL, destroyed_at = now() "
            "WHERE workspace = %s AND subject = %s",
            (workspace, subject),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Schema + helpers
# ─────────────────────────────────────────────────────────────────────────────
def apply_schema(conn) -> int:
    """Apply db/schema.sql (idempotent). Returns the number of statements executed."""
    import re

    path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(path, encoding="utf-8") as f:
        sql = f.read()
    sql = re.sub(r"--[^\n]*", "", sql)  # strip line comments (handles ';' inside comments)
    n = 0
    with conn.cursor() as cur:
        for stmt in sql.split(";"):
            if stmt.strip():
                cur.execute(stmt)
                n += 1
    return n


def to_vector(values) -> str:
    """Format an embedding as a CockroachDB VECTOR literal."""
    return "[" + ",".join(f"{float(v):.6f}" for v in values) + "]"


def logical_now(conn) -> str:
    """Current cluster logical timestamp — the anchor for AS OF SYSTEM TIME proofs."""
    with conn.cursor() as cur:
        cur.execute("SELECT cluster_logical_timestamp()::string")
        return cur.fetchone()[0]


@atexit.register
def _close_pool() -> None:
    """Close the pool cleanly at exit (avoids a thread-join warning during finalization)."""
    global _pool
    if _pool is not None:
        try:
            _pool.close()
        except Exception:
            pass
        _pool = None
