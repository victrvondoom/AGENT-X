"""
The database is unreachable. What does the product say?

`db/store.connect()` falls back to an offline stand-in so the console still
renders when CockroachDB cannot be reached. That fallback used to accept every
statement and do nothing: a POST that placed a legal hold, sealed a document or
appended an audit-chain row returned 200 OK having written nothing at all.

For a product whose entire claim is "there is a verifiable record of what
happened", a silent no-op write is the worst possible failure mode — worse than
a crash, because nobody finds out. These tests pin the rule:

    reads may degrade to empty; writes must fail, loudly, with a 503.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://localhost:5432/agentx")

from db import store  # noqa: E402


class TestStatementClassification:
    @pytest.mark.parametrize("sql", [
        "INSERT INTO documents (id) VALUES (%s)",
        "  update legal_holds set released = true",
        "DELETE FROM nodes WHERE subject = %s",
        "UPSERT INTO audit_chain (seq, hash) VALUES (%s, %s)",
        "CREATE TABLE IF NOT EXISTS cases (id STRING PRIMARY KEY)",
        "ALTER TABLE plans ADD COLUMN prior TEXT",
        "DROP TABLE scratch",
        "TRUNCATE timeline",
        "-- seed the chain\nINSERT INTO audit_chain VALUES (1)",
        "/* migration 007 */ CREATE TABLE case_outcomes (id STRING)",
        "WITH removed AS (SELECT id FROM nodes) DELETE FROM edges WHERE src IN "
        "(SELECT id FROM removed)",
    ])
    def test_writes_are_recognised(self, sql):
        assert store._is_write(sql) is True

    @pytest.mark.parametrize("sql", [
        "SELECT * FROM documents WHERE workspace = %s",
        "  select 1",
        "SHOW TABLES",
        "EXPLAIN SELECT * FROM nodes",
        "WITH recent AS (SELECT * FROM timeline) SELECT * FROM recent",
        "",
        "-- just a comment",
    ])
    def test_reads_are_recognised(self, sql):
        assert store._is_write(sql) is False


class TestOfflineCursor:
    def test_a_read_returns_honestly_empty(self):
        cur = store.MockCursor()
        cur.execute("SELECT subject FROM documents")
        assert cur.fetchone() is None
        assert cur.fetchall() == []

    def test_a_write_refuses_rather_than_pretending(self):
        cur = store.MockCursor()
        with pytest.raises(store.OfflineWriteError) as e:
            cur.execute("INSERT INTO legal_holds (subject) VALUES (%s)", ("alice",))
        assert "was not performed" in str(e.value)

    def test_the_error_names_the_statement_so_it_is_diagnosable(self):
        cur = store.MockCursor()
        with pytest.raises(store.OfflineWriteError) as e:
            cur.execute("UPSERT INTO audit_chain (seq) VALUES (1)")
        assert "UPSERT INTO audit_chain" in str(e.value)


class TestTheHttpAnswer:
    """End to end: with the database down, a write route must answer 503 with
    `written: false` — never 200, never a bare 500."""

    @pytest.fixture
    def offline_client(self, monkeypatch):
        def _no_pool():
            raise RuntimeError("cluster unreachable (simulated)")
        monkeypatch.setattr(store, "pool", _no_pool)
        os.environ["AGENT_X_ENGINE"] = "sqlite"
        from fastapi.testclient import TestClient
        from app.main import app
        return TestClient(app, raise_server_exceptions=False)

    @pytest.fixture
    def auth(self):
        tok = os.environ.get("AGENT_X_AUTH_TOKEN")
        return {"Authorization": f"Bearer {tok}"} if tok else {}

    def test_connect_falls_back_when_the_cluster_is_gone(self, monkeypatch):
        monkeypatch.setattr(store, "pool", lambda: (_ for _ in ()).throw(
            RuntimeError("unreachable")))
        with store.connect() as conn:
            assert store.is_offline(conn) is True

    def test_a_write_route_returns_503_not_200(self, offline_client, auth):
        r = offline_client.post("/api/hold", headers=auth, json={
            "subject": "alice", "reason": "litigation", "until": None,
            "workspace": "default"})
        assert r.status_code == 503, (
            f"a hold that was never stored answered {r.status_code}")
        body = r.json()
        assert body["error"] == "database_unreachable"
        assert body["written"] is False
        assert body["retryable"] is True

    def test_a_read_route_still_renders(self, offline_client):
        """The whole reason the offline fallback exists. Reads degrade to empty
        so the console is still usable while the cluster is down."""
        r = offline_client.get("/api/timeline")
        assert r.status_code == 200
        assert r.json() == []

    def test_health_reports_the_outage(self, offline_client):
        h = offline_client.get("/api/health").json()
        assert h["database"] != "connected"
        assert h["writes_persist"] is False
