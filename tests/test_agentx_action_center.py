"""
The Action Center — one query across every case a user has, surfacing whatever
needs a person's attention right now. Deliberately derived from live case/
approval/question state (see engine.action_items), never a separate
notifications table, so it can never disagree with what a case's own detail
page shows for the same case at the same moment.
"""
from __future__ import annotations

import pytest

from agentx import case as case_mod, engine, store
from agentx.execution import providers, runner


@pytest.fixture(autouse=True)
def sqlite_engine(tmp_path):
    store.reset_for_tests(str(tmp_path / "action_center.db"))
    providers.clear()
    providers.bootstrap()
    yield


@pytest.fixture
def conn():
    with store.connect() as c:
        yield c


def _case(conn, *, state: str, description: str = "test case",
          workspace: str = "default") -> dict:
    c = case_mod.create(conn, description=description, autonomy_level=2,
                        workspace=workspace)
    if state == "OPEN":
        return c
    # OPEN only declares INVESTIGATING/NEEDS_INPUT/WITHDRAWN; every other
    # target used by these tests (ACTION_REQUIRED, RESOLVED,
    # CLOSED_UNRESOLVED) is reachable from INVESTIGATING.
    c = case_mod.transition(conn, c["id"], "INVESTIGATING", why="test setup")
    if state != "INVESTIGATING":
        c = case_mod.transition(conn, c["id"], state, why="test setup")
    return c


class TestActionItems:
    def test_a_pending_approval_surfaces_as_approval_required(self, conn):
        c = _case(conn, state="ACTION_REQUIRED")
        runner.request_authorization(conn, c["id"], action="request_refund",
                                     prompt="Agent X wants to ask Kartly for a refund.")
        items = engine.action_items(conn)
        mine = [i for i in items if i["case_id"] == c["id"]]
        assert len(mine) == 1
        assert mine[0]["kind"] == "approval_required"
        assert "refund" in mine[0]["detail"]

    def test_an_open_question_surfaces_as_input_required(self, conn):
        c = _case(conn, state="NEEDS_INPUT")
        case_mod.ask(conn, c["id"], question="What was the exact charge date?",
                    why="needed to establish the window")
        items = engine.action_items(conn)
        mine = [i for i in items if i["case_id"] == c["id"]]
        assert len(mine) == 1
        assert mine[0]["kind"] == "input_required"
        assert "charge date" in mine[0]["detail"]

    def test_follow_up_required_surfaces_without_extra_rows(self, conn):
        c = _case(conn, state="INVESTIGATING")
        case_mod.transition(conn, c["id"], "ACTION_REQUIRED", why="t")
        case_mod.transition(conn, c["id"], "ACTION_SUBMITTED", why="t")
        case_mod.transition(conn, c["id"], "WAITING_EXTERNAL", why="t")
        case_mod.transition(conn, c["id"], "FOLLOW_UP_REQUIRED", why="t")
        items = engine.action_items(conn)
        mine = [i for i in items if i["case_id"] == c["id"]]
        assert len(mine) == 1 and mine[0]["kind"] == "follow_up_due"

    def test_resolved_case_surfaces_once(self, conn):
        c = _case(conn, state="INVESTIGATING")
        case_mod.transition(conn, c["id"], "RESOLVED", why="t")
        items = engine.action_items(conn)
        mine = [i for i in items if i["case_id"] == c["id"]]
        assert len(mine) == 1 and mine[0]["kind"] == "resolved"

    def test_closed_unresolved_and_withdrawn_do_not_surface(self, conn):
        c1 = _case(conn, state="CLOSED_UNRESOLVED")
        c2 = _case(conn, state="WITHDRAWN")
        items = engine.action_items(conn)
        ids_ = {i["case_id"] for i in items}
        assert c1["id"] not in ids_ and c2["id"] not in ids_

    def test_a_bare_open_case_with_nothing_pending_produces_no_item(self, conn):
        """OPEN and INVESTIGATING have no _ACTION_KIND entry — nothing to do yet
        is not the same as something needing attention."""
        c = _case(conn, state="INVESTIGATING")
        items = engine.action_items(conn)
        assert not [i for i in items if i["case_id"] == c["id"]]

    def test_granting_an_approval_removes_it_from_the_action_center(self, conn):
        """No separate table to go stale: once granted=True, the query that
        built the item no longer returns it."""
        c = _case(conn, state="ACTION_REQUIRED")
        auth = runner.request_authorization(conn, c["id"], action="request_refund",
                                            prompt="approve this")
        assert [i for i in engine.action_items(conn) if i["case_id"] == c["id"]]
        runner.decide_authorization(conn, auth["id"], granted=True)
        assert not [i for i in engine.action_items(conn) if i["case_id"] == c["id"]
                    and i["kind"] == "approval_required"]

    def test_items_are_scoped_to_workspace(self, conn):
        c = _case(conn, state="RESOLVED", description="other workspace case",
                 workspace="other-ws")
        items = engine.action_items(conn, workspace="default")
        assert c["id"] not in {i["case_id"] for i in items}
        items_other = engine.action_items(conn, workspace="other-ws")
        assert c["id"] in {i["case_id"] for i in items_other}


class TestActionCenterEndpoint:
    def test_endpoint_returns_items_for_a_real_case(self, tmp_path):
        os_env_db = str(tmp_path / "ac_http.db")
        import os
        os.environ.setdefault("DATABASE_URL", "postgresql://localhost:5432/agentx")
        os.environ["AGENT_X_ENGINE"] = "sqlite"
        store.use_sqlite(os_env_db)
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)

        token = os.environ.get("AGENT_X_AUTH_TOKEN")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        r = client.post("/api/agentx/cases", json={
            "description": "Kartly charged me twice for order 402-9938271.",
            "use_llm": False}, headers=headers)
        assert r.status_code == 200

        r2 = client.get("/api/agentx/action-center")
        assert r2.status_code == 200
        body = r2.json()
        assert "items" in body and isinstance(body["items"], list)
