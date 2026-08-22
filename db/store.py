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
import re as _re
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


class OfflineWriteError(RuntimeError):
    """Raised when a write is attempted while the database is unreachable.

    This exists because the alternative is worse. The offline fallback below
    used to accept every statement and do nothing, which meant a POST that
    sealed a document, recorded a consent decision or appended an audit-chain
    row returned 200 OK having written nothing at all. A product whose entire
    claim is "there is a verifiable record of what happened" cannot afford a
    code path that silently produces no record. Reads may degrade to empty —
    an empty list is honestly empty. Writes may not.
    """


# Statements that change state. Anything not in this set is treated as a read
# and allowed to return nothing while the database is unreachable.
_WRITE_VERBS = frozenset({
    "insert", "update", "delete", "upsert", "merge", "create", "alter", "drop",
    "truncate", "grant", "revoke", "comment", "import", "restore", "begin",
    "commit", "rollback", "set",
})


def _is_write(stmt) -> bool:
    """Best-effort read/write classification of a SQL statement."""
    text = (stmt if isinstance(stmt, str) else str(stmt)).lstrip()
    # strip leading line and block comments before reading the verb
    while True:
        if text.startswith("--"):
            nl = text.find("\n")
            text = text[nl + 1:].lstrip() if nl != -1 else ""
        elif text.startswith("/*"):
            close = text.find("*/")
            text = text[close + 2:].lstrip() if close != -1 else ""
        else:
            break
    if not text:
        return False
    verb = _re.split(r"[\s(;]", text, 1)[0].lower()
    if verb == "with":
        # CTEs are reads unless the body writes: WITH x AS (...) INSERT INTO ...
        return bool(_re.search(r"\b(insert|update|delete|upsert)\b", text, _re.I))
    return verb in _WRITE_VERBS


class MockCursor:
    """Read-only stand-in used when the database cannot be reached."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, stmt, params=None):
        if _is_write(stmt):
            raise OfflineWriteError(
                "the database is unreachable, so this write was not performed. "
                "Agent X refuses to report success for a record it did not "
                "write. Check DATABASE_URL and that your CockroachDB cluster is "
                "reachable, then retry. (statement: "
                f"{str(stmt)[:60].strip()}...)")

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class MockConnection:
    def cursor(self):
        return MockCursor()

    def commit(self):
        pass


def pool() -> ConnectionPool:
    """Process-wide connection pool with fast timeout and lazy open."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            DATABASE_URL,
            min_size=0,
            max_size=4,
            kwargs={"autocommit": True, "connect_timeout": 2},
            open=False,
            timeout=2.0,
        )
    return _pool


@contextmanager
def connect():
    """Borrow a pooled connection, falling back to MockConnection if the database
    is offline so the demo UI still renders.

    The acquire is wrapped, the YIELD is not. Wrapping the yield in the same
    `try` meant any exception raised inside the caller's `with store.connect()`
    block — a constraint violation, a bug in application code — was thrown back
    into this generator at the yield point, caught by the blanket `except`, and
    answered by yielding a SECOND time. A generator that yields twice from one
    contextmanager raises `RuntimeError: generator didn't stop after throw()`,
    which replaced the caller's real exception with an unrelated one and defeated
    any targeted `except` around the block.

    The fallback is READ-ONLY. Reads return honestly empty; any write raises
    `OfflineWriteError`, which `app/main.py` answers as a 503 carrying
    `written: false`. It used to accept writes and do nothing, which meant a
    sealed document or an audit-chain append could return 200 OK having written
    nothing — the one failure mode this product cannot have. Callers that need
    to check first can use `is_offline`; the resolution engine in
    `agentx/store.py` refuses this fallback entirely.
    """
    conn = None
    try:
        p = pool()
        if not getattr(p, "_opened", True) and hasattr(p, "open"):
            try:
                p.open(wait=False)
            except Exception:
                pass
        cm = p.connection(timeout=1.5)
        conn = cm.__enter__()
    except Exception:
        cm, conn = None, MockConnection()

    if cm is None:
        yield conn
        return
    with cm:
        yield conn


def is_offline(conn) -> bool:
    """True when `conn` is the offline stand-in rather than a real connection."""
    return isinstance(conn, MockConnection)





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
