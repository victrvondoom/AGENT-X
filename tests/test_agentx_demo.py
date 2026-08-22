"""
The demonstration engine — five real problems, resolved end to end.

Not scripted: each test runs the actual pipeline (understanding → evidence →
policy → eligibility → planning → governor → execution → verification → receipt)
against the deterministic sandbox and asserts on what genuinely happened.
"""
from __future__ import annotations

import pytest

from agentx import chain, demo, store
from agentx.execution import providers


@pytest.fixture(autouse=True)
def sqlite_engine(tmp_path):
    store.reset_for_tests(str(tmp_path / "agentx_test.db"))
    providers.clear()
    providers.bootstrap()
    yield


@pytest.fixture
def conn():
    with store.connect() as c:
        demo.reset(c)
        yield c


@pytest.mark.parametrize("key", sorted(demo.SCENARIOS))
def test_every_scenario_resolves_and_produces_an_intact_signed_chain(conn, key):
    r = demo.run(conn, key, use_llm=False)
    assert r["final_state"] == "RESOLVED", (
        f"scenario {key} ended in {r['final_state']}, events: "
        f"{[e['detail'] for e in r['events']]}")
    assert r["chain"]["ok"]
    assert r["receipt_signed"]


def test_scenario_a_actually_hits_a_refusal_before_resolving(conn):
    """Kartly stalls a duplicate-charge claim; the demo must show a real chase and
    escalation, not a first-try success — otherwise it proves nothing about the
    follow-up agent or the escalation ladder."""
    r = demo.run(conn, "A", use_llm=False)
    stages = [e["stage"] for e in r["events"]]
    assert "follow-up" in stages
    escalated = any("escalate" in (e.get("detail") or "") for e in r["events"])
    assert escalated


def test_scenario_b_grant_is_conditioned_on_citing_the_right(conn):
    """Streamly's sandbox policy is to refuse a generic renewal-refund ask and
    relent only when the letter cites a right it recognises (uk_cra_2015 /
    uk_ccr_2013 / ROSCA). Agent X's letter is composed FROM the applicable-policy
    analysis, so the citation is present from the first submission and the
    refusal never has to fire — which is the stronger demonstration that
    establishing entitlement before writing actually pays off, not a weaker one
    that merely shows a later relent."""
    r = demo.run(conn, "B", use_llm=False)
    refund = next(e for e in r["snapshot"]["executions"]
                 if e["action"] == "request_refund")
    assert refund["data"].get("cited_rights"), (
        "the letter Agent X sent cited none of Streamly's recognised rights")
    assert refund["data"]["status"] == "approved"


def test_sandbox_state_persists_across_the_run(conn):
    from agentx.sandbox import world
    demo.run(conn, "A", use_llm=False)
    objects = world.all_objects(conn, "kartly")
    assert any(o["kind"] == "ticket" for o in objects)


def test_sandbox_actions_are_labelled_sandbox_on_the_receipt(conn):
    r = demo.run(conn, "A", use_llm=False)
    executions = r["snapshot"]["executions"]
    external = [e for e in executions if e.get("external_ref")]
    assert external
    assert all(e["provider_mode"] == "sandbox" for e in external)


def test_ambiguity_probe_holds_several_readings_for_a_generic_sentence():
    out = demo.ambiguity_probe()
    assert out["ambiguous"]
    assert len(out["interpretations"]) >= 4
    assert out["best_question"] is not None


def test_reset_clears_cases_and_sandbox_state(conn):
    demo.run(conn, "A", use_llm=False)
    demo.reset(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM cases")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM sandbox_objects")
        assert cur.fetchone()[0] == 0
