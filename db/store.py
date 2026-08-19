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
            # destroyed_at is the ONLY proof of erasure. A row can exist with no key yet —
            # a legal hold placed before any data arrived creates exactly that — and
            # treating a null key as erasure would permanently brick the subject name.
            if destroyed is not None:
                raise KeyDestroyed(subject)
            if wrapped is not None:
                return _open(_root_key(), wrapped)
            # row exists, no key yet: fall through and mint one into it.
        # Mint a key but only persist it if none exists yet (ON CONFLICT DO NOTHING), then re-read
        # the WINNING key — so two concurrent first-ingests converge on one key instead of one of
        # them encrypting a document under a key that gets overwritten (and thus lost forever).
        dek = AESGCM.generate_key(bit_length=256)
        cur.execute(
            # DO UPDATE, guarded, rather than DO NOTHING: the row may already exist without a
            # key (a hold placed before any data), and DO NOTHING would leave it keyless
            # forever. The WHERE clause keeps this race-safe — only one concurrent writer can
            # fill a null key — and `destroyed_at IS NULL` makes it impossible to resurrect a
            # crypto-shredded subject.
            "INSERT INTO subject_keys (workspace, subject, wrapped_dek) VALUES (%s, %s, %s) "
            "ON CONFLICT (workspace, subject) DO UPDATE SET wrapped_dek = excluded.wrapped_dek "
            "WHERE subject_keys.wrapped_dek IS NULL AND subject_keys.destroyed_at IS NULL",
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
# Statements whose success depends on the cluster, not on the schema being correct.
# Vector indexing is on by default on CockroachDB Cloud but OFF on a self-hosted
# v25.3 node, and SET CLUSTER SETTING is refused on managed clusters. Neither is
# fatal: without the index, ANN recall still returns correct results, just by scan
# rather than lookup join. Aborting here used to leave the database half-built —
# every table after the vector index (edges, subject_keys, erasure_events,
# workspaces, timeline) was silently never created.
_OPTIONAL = ("CREATE VECTOR INDEX", "SET CLUSTER SETTING")


def apply_schema(conn) -> int:
    """Apply db/schema.sql (idempotent). Returns the number of statements executed.

    Raises on any real schema error. Environment-dependent statements (see _OPTIONAL)
    are attempted, and a failure is recorded on `apply_schema.warnings` rather than
    aborting the rest of the schema.
    """
    import re

    path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(path, encoding="utf-8") as f:
        sql = f.read()
    sql = re.sub(r"--[^\n]*", "", sql)  # strip line comments (handles ';' inside comments)
    n = 0
    warnings: list[str] = []
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        if not stmt:
            continue
        head = " ".join(stmt.split())[:60]
        optional = any(k in stmt.upper() for k in _OPTIONAL)
        try:
            # A fresh cursor per statement: a failed statement poisons its cursor, and
            # one optional failure must not take the remaining schema down with it.
            with conn.cursor() as cur:
                cur.execute(stmt)
            n += 1
        except Exception as e:
            if not optional:
                raise
            warnings.append(f"{head} -> {str(e).splitlines()[0][:120]}")
    apply_schema.warnings = warnings
    return n


apply_schema.warnings = []


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
