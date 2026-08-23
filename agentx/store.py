"""
Agent X persistence — one logical store, two engines, no dialect fork.

Agent X was built against CockroachDB and uses it for things nothing else does:
`AS OF SYSTEM TIME` as an erasure receipt, a C-SPANN vector index in the same
table as the relational data, one serializable cascade across graph + vectors +
documents. None of that is negotiable and none of it is removed.

But Agent X is a consumer product, and a consumer product that cannot be run without
a cloud database is a consumer product nobody can run. So the case layer — cases,
evidence, facts, plans, executions, the per-case hash chain — is written against a
deliberately portable SQL subset and can run on either:

    cockroachdb   the full system. Everything below plus AS OF SYSTEM TIME,
                  vector recall, and the erasure cascade.
    sqlite        the case layer only, in a local file. Real ACID transactions,
                  real hash chains, real crypto — but no time-travel proof and no
                  vector index, and `describe()` says so out loud.

The rule this module exists to enforce: the engine in use is never implied and
never guessed at. `describe()` returns which one is live and which guarantees come
with it, and that string is rendered in the UI and stamped into every resolution
receipt. A receipt from the local engine must not be mistakable for one backed by
an object-locked certificate and a time-travel proof.

Why not just let the existing `db.store.connect()` fall through? Because it yields
a MockConnection that silently swallows writes when the database is unreachable.
For browsing a demo that is a kindness; for a case file it is a lie, and Agent X's
entire claim is that it does not lie about what happened.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from contextlib import contextmanager

_MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "db", "migrations")
# Applied in order, on whichever engine is live. Both are written in the portable
# subset documented in 005; the trust spine's own migrations (001-004) are
# CockroachDB-side and are applied by scripts/init_trust.py, not from here.
MIGRATIONS = ("005_agentx_cases.sql", "006_sandbox.sql", "007_outcomes.sql",
              "008_case_clock.sql", "009_research.sql")

# Where the local engine keeps its file. Overridable so tests get their own.
DEFAULT_SQLITE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "agentx.db")

_state: dict = {"engine": None, "sqlite_path": None, "ready": False, "reason": ""}
_lock = threading.RLock()


# ─────────────────────────────────────────────────────────────────────────────
# engine selection
# ─────────────────────────────────────────────────────────────────────────────
def _probe_cockroach() -> tuple[bool, str]:
    """Is the configured DATABASE_URL actually reachable right now?

    Probed once, at first use, with a short timeout. A consumer opening a case
    should not wait on a TCP timeout to find out the cloud database is down — and
    more importantly, should not have their case silently discarded because of it.
    """
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn or "USER:PASSWORD@HOST" in dsn:
        return False, "DATABASE_URL is not configured"
    try:
        import psycopg
        with psycopg.connect(dsn, connect_timeout=3, autocommit=True) as c:
            with c.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True, "connected"
    except Exception as e:
        return False, str(e).splitlines()[0][:160]


def select_engine(force: str | None = None) -> str:
    """Pick and remember the engine. `force` is for tests and the demo harness."""
    with _lock:
        if force:
            _state.update(engine=force, ready=False,
                          reason=f"forced by caller ({force})")
            return force
        if _state["engine"]:
            return _state["engine"]
        prefer = (os.environ.get("AGENT_X_ENGINE") or "auto").lower()
        if prefer == "sqlite":
            _state.update(engine="sqlite", reason="AGENT_X_ENGINE=sqlite")
        elif prefer == "cockroachdb":
            _state.update(engine="cockroachdb", reason="AGENT_X_ENGINE=cockroachdb")
        else:
            ok, why = _probe_cockroach()
            _state.update(engine="cockroachdb" if ok else "sqlite",
                          reason=why if ok else f"local engine: {why}")
        return _state["engine"]


def sqlite_path() -> str:
    return _state["sqlite_path"] or os.environ.get("AGENT_X_DB_PATH") or DEFAULT_SQLITE_PATH


def use_sqlite(path: str) -> None:
    """Point the local engine at a specific file (tests, demo resets)."""
    with _lock:
        _state.update(engine="sqlite", sqlite_path=path, ready=False,
                      reason=f"local engine at {path}")


def describe() -> dict:
    """What is running, and what it can and cannot prove.

    Rendered in the UI and embedded in every resolution receipt. The `guarantees`
    list is the honest one: it says what this engine actually gives you, not what
    the product could give you on someone else's hardware.
    """
    eng = select_engine()
    try:
        from agentx import sealing
        key_source = sealing.root_key_source()
        sign_source = sealing.signing_key_source()
    except Exception:
        key_source = sign_source = "unknown"
    if eng == "cockroachdb":
        return {
            "engine": "cockroachdb",
            "reason": _state["reason"],
            "root_key": key_source,
            "signing_key": sign_source,
            "guarantees": [
                "serializable transactions",
                "AS OF SYSTEM TIME proof-of-prior-existence",
                "distributed vector index (C-SPANN) for recall",
                "row-level TTL retention",
                "per-subject crypto-shred over the shared subject_keys table",
            ],
            "not_available": [],
        }
    return {
        "engine": "sqlite",
        "reason": _state["reason"],
        "root_key": key_source,
        "signing_key": sign_source,
        "guarantees": [
            "ACID transactions",
            "hash-chained, gap-free case audit chain",
            "ECDSA P-256 resolution receipts",
            "per-case crypto-shred (envelope encryption, key destruction)",
        ],
        "not_available": [
            "AS OF SYSTEM TIME proof-of-prior-existence (CockroachDB only)",
            "vector-index recall (CockroachDB only)",
            "object-locked S3 certificates (requires AWS configuration)",
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# the SQLite shim — psycopg-shaped, so one body of SQL serves both engines
# ─────────────────────────────────────────────────────────────────────────────
_PARAM = re.compile(r"%s")


def _translate(sql: str) -> str:
    """Rewrite psycopg's %s placeholders to sqlite3's ?, ignoring string literals.

    Naive `.replace('%s', '?')` corrupts any SQL containing a literal percent —
    `LIKE 'case_%%'` being the one already in this repository — so the scan tracks
    quote state. Cheap, and the alternative (two copies of every statement) is the
    thing this module exists to avoid.
    """
    out, i, in_str = [], 0, False
    while i < len(sql):
        ch = sql[i]
        if ch == "'":
            in_str = not in_str
            out.append(ch)
            i += 1
        elif not in_str and sql.startswith("%s", i):
            out.append("?")
            i += 2
        elif not in_str and sql.startswith("%%", i):
            out.append("%")
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


class _SqliteCursor:
    """Just enough of a psycopg cursor for the case layer."""

    def __init__(self, cur: sqlite3.Cursor):
        self._cur = cur

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self._cur.close()
        return False

    def execute(self, sql: str, params=None):
        self._cur.execute(_translate(sql), tuple(params) if params else ())
        return self

    def executemany(self, sql: str, seq):
        self._cur.executemany(_translate(sql), [tuple(p) for p in seq])
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def rowcount(self):
        return self._cur.rowcount

    def close(self):
        self._cur.close()


class _SqliteConn:
    def __init__(self, raw: sqlite3.Connection):
        self._raw = raw

    def cursor(self):
        return _SqliteCursor(self._raw.cursor())

    @contextmanager
    def transaction(self):
        """Match psycopg's `with conn.transaction():` block semantics."""
        self._raw.execute("BEGIN")
        try:
            yield self
        except BaseException:
            self._raw.rollback()
            raise
        else:
            self._raw.commit()

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        self._raw.close()


def _open_sqlite() -> _SqliteConn:
    path = sqlite_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    raw = sqlite3.connect(path, timeout=15.0, isolation_level=None,
                          check_same_thread=False)
    raw.execute("PRAGMA journal_mode=WAL")
    raw.execute("PRAGMA foreign_keys=ON")
    raw.execute("PRAGMA busy_timeout=15000")
    return _SqliteConn(raw)


# ─────────────────────────────────────────────────────────────────────────────
# connections
# ─────────────────────────────────────────────────────────────────────────────
@contextmanager
def connect():
    """A connection to whichever engine is live. Raises if neither can be opened.

    Deliberately NOT the tolerant fallback that `db.store.connect()` uses. A case
    write that cannot land must fail loudly: the whole product rests on the record
    being complete, and a silently-dropped execution record is worse than an error.
    """
    ensure_schema()
    if select_engine() == "cockroachdb":
        from db import store as _crdb
        with _crdb.pool().connection(timeout=10.0) as conn:
            yield conn
    else:
        conn = _open_sqlite()
        try:
            yield conn
        finally:
            conn.close()


def ensure_schema() -> dict:
    """Apply the case-layer DDL once per process. Idempotent on both engines."""
    with _lock:
        if _state["ready"]:
            return {"engine": _state["engine"], "applied": False}
        eng = select_engine()
        statements: list[str] = []
        for name in MIGRATIONS:
            with open(os.path.join(_MIGRATIONS_DIR, name), encoding="utf-8") as f:
                sql = re.sub(r"--[^\n]*", "", f.read())
            statements += [s.strip() for s in sql.split(";") if s.strip()]

        if eng == "cockroachdb":
            from db import store as _crdb
            with _crdb.pool().connection(timeout=10.0) as conn:
                _apply(conn, statements)
        else:
            conn = _open_sqlite()
            try:
                # schema_migrations belongs to the trust spine's migration 001,
                # which the local engine never runs. Created here so the tail of
                # every migration file works unchanged on both engines.
                with conn.cursor() as cur:
                    cur.execute("CREATE TABLE IF NOT EXISTS schema_migrations ("
                                "version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ)")
                _apply(conn, statements)
                conn.commit()
            finally:
                conn.close()
        _state["ready"] = True
        return {"engine": eng, "applied": True, "statements": len(statements)}


# Re-running a migration must be a no-op. `CREATE TABLE/INDEX IF NOT EXISTS` is
# portable; `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` is not — CockroachDB
# supports it, SQLite does not. Rather than fork the DDL, an ALTER that fails
# only because the column is already there is treated as the success it
# effectively is. Any OTHER failure still raises: a genuinely broken migration
# must not be swallowed.
_ALREADY_EXISTS = ("duplicate column", "already exists")


def _apply(conn, statements: list[str]) -> None:
    for stmt in statements:
        try:
            with conn.cursor() as cur:
                cur.execute(stmt)
        except Exception as e:
            msg = str(e).lower()
            if stmt.lstrip().upper().startswith("ALTER") and                     any(m in msg for m in _ALREADY_EXISTS):
                continue
            raise


def reset_for_tests(path: str | None = None) -> str:
    """Point at a fresh database file and forget the applied-schema flag.

    A FILE, never ":memory:". `connect()` opens a new connection per call, and an
    in-memory SQLite database is private to its connection — so an in-memory test
    harness would silently lose every write between calls and every test would
    pass against an empty database.
    """
    import tempfile
    with _lock:
        target = path or os.path.join(
            tempfile.mkdtemp(prefix="agentx-test-"), "agentx.db")
        _state.update(engine="sqlite", sqlite_path=target, ready=False,
                      reason="test harness")
    return target


# ─────────────────────────────────────────────────────────────────────────────
# small helpers every case module needs
# ─────────────────────────────────────────────────────────────────────────────
def jdump(obj) -> str:
    """Canonical JSON for storage. Sorted and compact, matching the trust spine's
    canonicalisation, so a column that later lands in a hash is already the exact
    bytes that will be hashed."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def jload(raw, default=None):
    if raw in (None, ""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def rows_to_dicts(cur, rows) -> list[dict]:
    """psycopg and sqlite3 both expose `cursor.description`; use it rather than
    positional unpacking so adding a column does not silently shift every field."""
    cols = [d[0] for d in cur._cur.description] if isinstance(cur, _SqliteCursor) \
        else [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]
