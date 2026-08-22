"""
The Case abstraction — state machine enforcement, and the question-answering
path that an MCP-connected LLM caller makes much more likely to be exercised
with a wrong or mismatched id than a UI ever does.
"""
from __future__ import annotations

import pytest

from agentx import case as case_mod
from agentx import chain, engine, store
from agentx.execution import providers


@pytest.fixture(autouse=True)
def sqlite_engine(tmp_path):
    store.reset_for_tests(str(tmp_path / "case_test.db"))
    providers.clear()
    providers.bootstrap()
    yield


@pytest.fixture
def conn():
    with store.connect() as c:
        yield c


class TestStateMachine:
    def test_declared_transition_succeeds(self, conn):
        c = case_mod.create(conn, description="test")
        out = case_mod.transition(conn, c["id"], "INVESTIGATING", why="test")
        assert out["state"] == "INVESTIGATING"

    def test_undeclared_transition_is_refused(self, conn):
        c = case_mod.create(conn, description="test")
        with pytest.raises(case_mod.InvalidTransition):
            case_mod.transition(conn, c["id"], "RESOLVED", why="test")

    def test_terminal_state_accepts_no_further_transition(self, conn):
        c = case_mod.create(conn, description="test")
        case_mod.transition(conn, c["id"], "WITHDRAWN", why="test")
        with pytest.raises(case_mod.InvalidTransition):
            case_mod.transition(conn, c["id"], "INVESTIGATING", why="test")


class TestAnswerQuestion:
    def test_unknown_case_raises_cleanly_with_no_partial_write(self, conn):
        """Regression: engine.answer_question() used to look up the case AFTER
        case_mod.answer() had already mutated the question row, so a bad
        case_id crashed with an unhandled TypeError partway through, leaving
        the question marked ANSWERED. It must now fail before touching
        anything, with a clean ValueError."""
        c = case_mod.create(conn, description="test")
        qid = case_mod.ask(conn, c["id"], question="q?", qid=f"{c['id']}:q1")["id"]

        with pytest.raises(ValueError, match="no such case"):
            engine.answer_question(conn, "PX-DOES-NOT-EXIST", qid, "yes")

        # The question must still be OPEN — no partial mutation from the failed call.
        rows = case_mod.open_questions(conn, c["id"])
        assert any(r["id"] == qid for r in rows)

    def test_unknown_question_raises_cleanly(self, conn):
        c = case_mod.create(conn, description="test")
        with pytest.raises(ValueError, match="no such question"):
            case_mod.answer(conn, c["id"], "does-not-exist", "yes")

    def test_mismatched_case_and_question_is_refused(self, conn):
        """Regression: case_mod.answer() used to look up and update a question by
        its own id alone, ignoring whether it actually belonged to the case_id
        passed in, and would write the audit entry to the WRONG case's chain.
        An MCP-connected caller can plausibly pass a stale or mismatched pair,
        so this must be a hard refusal, not a silent misattribution."""
        a = case_mod.create(conn, description="case A")
        b = case_mod.create(conn, description="case B")
        qid_b = case_mod.ask(conn, b["id"], question="q on B?",
                             qid=f"{b['id']}:q1")["id"]

        with pytest.raises(ValueError, match="belongs to case"):
            case_mod.answer(conn, a["id"], qid_b, "yes")

        # B's question must still be open, and A's chain must not have gained a
        # question.answered row for a question that isn't its own.
        assert any(r["id"] == qid_b for r in case_mod.open_questions(conn, b["id"]))
        a_chain = chain.readable(conn, a["id"])
        assert not any(r["step"] == "question.answered" for r in a_chain)

    def test_matching_case_and_question_succeeds(self, conn):
        c = case_mod.create(conn, description="test")
        qid = case_mod.ask(conn, c["id"], question="q?", qid=f"{c['id']}:q1")["id"]
        out = case_mod.answer(conn, c["id"], qid, "yes")
        assert out["answer"] == "yes"
        assert not any(r["id"] == qid for r in case_mod.open_questions(conn, c["id"]))


class TestLegacyStoreContextManager:
    """Regression for db/store.py:connect(), Agent X's original memory-engine
    connection helper. Its try/except used to wrap the `yield`, so an exception
    raised inside a caller's `with` block was thrown back into the generator,
    caught, and answered by yielding a second time — surfacing as
    `RuntimeError: generator didn't stop after throw()` and destroying the
    caller's real exception type."""

    def test_caller_exception_propagates_with_its_own_type(self):
        from db import store as legacy
        with pytest.raises(KeyError):
            with legacy.connect():
                raise KeyError("the caller's real error")

    def test_offline_fallback_is_detectable(self):
        from db import store as legacy
        with legacy.connect() as conn:
            # No CockroachDB in the test environment, so this is the stand-in —
            # and the point is that a caller can TELL, rather than silently
            # writing into a no-op cursor.
            assert legacy.is_offline(conn) is True
