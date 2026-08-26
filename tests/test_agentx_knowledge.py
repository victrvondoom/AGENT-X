"""
The research layer: retrieval that declines, and citation checking that catches
the failure a word-overlap check cannot.

Two properties are worth pinning here, and they pull in opposite directions:

  * the corpus must be REACHED when it has something — otherwise the research
    layer is decoration;
  * the corpus must NOT be reached when it does not — otherwise a hotel billing
    dispute gets answered with airline regulations, which is worse than silence
    because it is fluent.

`test_retrieval_calibration_holds_both_directions` asserts both against the same
threshold, so tuning the floor to fix one direction fails the other.
"""
from __future__ import annotations

import pytest

from agentx import knowledge
from agentx.knowledge import corpus, retrieve, verify


# ─────────────────────────────────────────────────────────── corpus
def test_corpus_loads_and_is_segmented():
    stats = knowledge.stats()
    assert stats["available"], "the checked-in corpus should load"
    assert stats["documents"] > 0
    assert stats["passages"] >= stats["documents"], "documents segment into passages"


def test_every_passage_can_name_its_source():
    """A passage Agent X cannot attribute is a passage it must not cite."""
    for p in corpus.passages():
        assert p.title.strip(), f"{p.id} has no title"
        assert p.text.strip(), f"{p.id} has no text"
        assert p.citation.strip(), f"{p.id} cannot be cited"


def test_sectors_reported_are_the_sectors_present():
    """`stats()` must not advertise coverage the corpus does not have.

    The source project claimed eight domains; five survived extraction with real
    authored content. Reporting the other three would be the exact dishonesty
    this codebase refuses elsewhere.
    """
    assert set(knowledge.sectors()) == set(knowledge.stats()["sectors"])


# ─────────────────────────────────────────────────────────── retrieval
RETRIEVABLE = [
    "unauthorized transaction on my credit card, bank refuses to reverse it",
    "flight cancelled without notice, what compensation am I owed",
    "builder has not given possession of my flat after 3 years",
    "landlord will not return my security deposit",
    "RTI application ignored, no reply from the department",
    "airline lost my baggage on an international flight",
]

# Full-length case narratives — what `engine.investigate` actually searches with.
# Kept as a separate list because query LENGTH is the property that broke an
# earlier design: these carry proper nouns, reference numbers and amounts that
# appear in no corpus, and a ratio-based floor scored them below a bare keyword
# query saying the same thing.
NARRATIVES = [
    "My flight from Delhi was cancelled two hours before departure and the "
    "airline refuses to pay compensation. Booking ref AI4592, I paid 12400 INR "
    "on 2026-07-02.",
    "I was charged twice for the same order on my credit card, the bank will "
    "not reverse it. 4500 INR on 2026-07-11.",
    "My landlord is refusing to return my security deposit after I moved out "
    "of the flat in Whitefield last month.",
]

# Real consumer problems that this corpus genuinely does not cover. Retrieving
# anything for these means retrieving something wrong.
NOT_IN_CORPUS = [
    "my hotel in Paris overcharged me for the minibar",
    "my gym membership auto-renewed and I want it cancelled",
    "netflix charged me twice this month",
    "the restaurant gave me food poisoning",
    # The hardest negative in the set: a hotel cancellation shares "cancelled"
    # and "booking" with airline cancellation guidance, which is genuinely
    # adjacent and genuinely wrong.
    "My hotel in Paris cancelled my booking on arrival and I had to pay more "
    "elsewhere.",
]


@pytest.mark.parametrize("query", RETRIEVABLE)
def test_retrieves_when_the_corpus_covers_it(query):
    assert knowledge.search(query), f"expected a passage for {query!r}"


@pytest.mark.parametrize("query", NARRATIVES)
def test_retrieval_survives_a_full_case_narrative(query):
    """Adding "booking ref AI4592" to a complaint must not hide the regulation.

    The gate is absolute matched mass precisely so that irrelevant detail in the
    user's own words cannot suppress a relevant passage.
    """
    assert knowledge.search(query), (
        f"a real narrative retrieved nothing: {query[:60]!r}")


@pytest.mark.parametrize("query", NOT_IN_CORPUS)
def test_returns_nothing_rather_than_the_nearest_thing(query):
    hits = knowledge.search(query)
    assert hits == [], (
        f"{query!r} is not covered by this corpus, but retrieved "
        f"{[h['title'] for h in hits]}")


def test_retrieval_calibration_holds_both_directions():
    """The floor must separate covered queries from uncovered ones, with margin.

    Pinned as a separation rather than as two pass/fail lists so that moving
    MIN_MATCHED_MASS to rescue one direction visibly breaks the other. This is
    the test that fails if the corpus grows enough to shift the IDF scale.
    """
    covered = [_best_mass(q) for q in RETRIEVABLE + NARRATIVES]
    uncovered = [_best_mass(q) for q in NOT_IN_CORPUS]
    assert min(covered) > max(uncovered), (
        f"no separating threshold: weakest covered={min(covered):.2f}, "
        f"strongest uncovered={max(uncovered):.2f}")
    assert max(uncovered) < retrieve.MIN_MATCHED_MASS <= min(covered), (
        f"MIN_MATCHED_MASS={retrieve.MIN_MATCHED_MASS} is outside the gap "
        f"({max(uncovered):.2f} .. {min(covered):.2f})")


def _best_mass(query: str) -> float:
    """Highest matched IDF mass any passage reaches, ignoring the gates.

    Measured directly rather than through `search()`, whose whole job is to hide
    the passages below the floor — the calibration test needs to see them.
    """
    import math
    ps, tfs, _lengths, df, _avg = retrieve._index()
    terms = list(dict.fromkeys(retrieve.tokenize(query)))
    total = len(ps)
    idfs = {t: max(0.0, math.log(1 + (total - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5)))
            for t in terms}
    return max((sum(idfs[t] for t in terms if tf.get(t)) for tf in tfs), default=0.0)


def test_results_are_deterministic():
    """The same query must retrieve the same passages forever — a case's chain
    records what was retrieved, and a record that cannot be reproduced is not a
    record."""
    query = RETRIEVABLE[0]
    first = knowledge.search(query, limit=4)
    for _ in range(3):
        assert [h["id"] for h in knowledge.search(query, limit=4)] == \
               [h["id"] for h in first]


def test_sector_filter_restricts_results():
    hits = knowledge.search("compensation for a cancelled flight",
                            sectors=("housing",))
    assert all(h["sector"] == "housing" for h in hits)


def test_unmapped_domain_searches_everything():
    """A domain with no declared sector mapping must not be forced into one."""
    assert knowledge.sectors_for_domain("travel") == ("airlines",)
    assert knowledge.sectors_for_domain("hospitality") == ()
    assert knowledge.sectors_for_domain(None) == ()


def test_empty_query_retrieves_nothing():
    assert knowledge.search("") == []
    assert knowledge.search("   the and of   ") == []


# ─────────────────────────────────────────────────────────── citation checking
SOURCE = [{
    "id": "banking-x#0",
    "text": ("The bank must complete a shadow reversal of the disputed amount "
             "within 10 working days of the customer reporting an unauthorized "
             "electronic transaction. Zero liability applies where the customer "
             "notifies the bank within 3 working days."),
}]


def test_supported_claim_verifies():
    check = verify.verify_citation(
        "shadow reversal of the disputed amount within 10 working days", SOURCE)
    assert check.verdict == "verified"
    assert check.safe_to_state


def test_verbatim_claim_verifies():
    check = verify.verify_citation("Zero liability applies", SOURCE)
    assert check.verdict == "verified"


def test_claim_with_no_support_is_unsupported():
    check = verify.verify_citation(
        "the airline must provide hotel accommodation and meal vouchers", SOURCE)
    assert check.verdict == "unsupported"
    assert not check.safe_to_state


def test_wrong_figure_is_conflicting_not_verified():
    """The failure a word-overlap check passes.

    Every word of this claim except the number appears in the source, so a
    ratio-based check verifies it. The source says ten working days; this says
    thirty. Presenting that to a bank as an established right loses the case.
    """
    check = verify.verify_citation(
        "shadow reversal of the disputed amount within 30 working days", SOURCE)
    assert check.verdict == "conflicting", (
        f"expected conflicting, got {check.verdict}: {check.because}")
    assert "30" in check.unmatched_figures
    assert not check.safe_to_state


def test_on_topic_but_unestablished_is_partial():
    check = verify.verify_citation(
        "the customer may escalate an unauthorized transaction to the "
        "ombudsman and claim punitive damages for mental agony", SOURCE)
    assert check.verdict in ("partial", "unsupported")
    assert not check.safe_to_state


def test_generic_claim_is_not_flagged_as_unsupported():
    """A claim too short to carry a proposition is not evidence of fabrication."""
    check = verify.verify_citation("RBI rules", SOURCE)
    assert check.verdict == "partial"
    assert not check.safe_to_state


def test_no_sources_means_unsupported():
    check = verify.verify_citation("anything at all", [])
    assert check.verdict == "unsupported"


def test_every_verdict_is_a_declared_verdict():
    claims = ["shadow reversal within 10 working days",
              "within 30 working days", "unrelated airline baggage rule", "RBI"]
    for check in verify.verify_citations(claims, SOURCE):
        assert check.verdict in verify.VERDICTS
        assert check.because.strip()


def test_only_verified_is_safe_to_state():
    """The gate the letter composer depends on."""
    for verdict in verify.VERDICTS:
        check = verify.CitationCheck(claim="c", verdict=verdict, because="b",
                                     matched_ratio=0.0)
        assert check.safe_to_state == (verdict == "verified")


# ─────────────────────────────────────────────────────────── research on a case
@pytest.fixture(autouse=True)
def _sqlite(tmp_path):
    from agentx import store
    store.reset_for_tests(str(tmp_path / "research_test.db"))
    yield


@pytest.fixture
def conn():
    from agentx import store
    with store.connect() as c:
        yield c


def _investigated(conn, description: str) -> dict:
    from agentx import engine
    snap = engine.intake(conn, description=description, use_llm=False)
    return engine.investigate(conn, snap["case"]["id"], use_llm=False)


def test_research_is_attached_to_a_case(conn):
    snap = _investigated(conn, "My flight from Delhi was cancelled two hours "
                               "before departure and the airline refuses to pay "
                               "compensation. Booking ref AI4592.")
    sources = snap["research"]["sources"]
    assert sources, "an airline cancellation should reach the airlines corpus"
    assert all(s["title"] for s in sources)


def test_sources_are_sector_coherent(conn):
    """Every source on a case comes from one regulatory regime.

    A cancelled-flight case that lists a RERA housing complaint template as its
    fourth source is not showing corroboration, it is showing noise — and a
    reader counting "4 sources" would take it for the former.
    """
    snap = _investigated(conn, "My flight from Delhi was cancelled two hours "
                               "before departure and the airline refuses to pay "
                               "compensation.")
    sectors = {s["sector"] for s in snap["research"]["sources"]}
    assert len(sectors) <= 1, f"sources drawn from several regimes: {sectors}"


def test_sources_are_returned_in_relevance_order(conn):
    snap = _investigated(conn, "I was charged twice for the same order on my "
                               "credit card and the bank will not reverse it.")
    scores = [s["score"] for s in snap["research"]["sources"]]
    assert scores == sorted(scores, reverse=True), scores


def test_one_document_is_not_counted_as_several_sources(conn):
    snap = _investigated(conn, "The airline lost my checked baggage on an "
                               "international flight and has not responded.")
    ids_ = [s["passage_id"].split("#")[0] for s in snap["research"]["sources"]]
    assert len(ids_) == len(set(ids_)), "the same document was listed twice"


def test_an_unclassifiable_case_still_gets_research(conn):
    """The case research helps most: Agent X's ontology has no housing problem
    type, so there is no entitlement to compute — but the complaint route exists
    and the user should still be told it.

    The case itself no longer stalls at problem_type=None (see
    tests/test_agentx_general_fallback.py — it falls back to
    general_consumer_problem and still reaches a plan), but research must still
    reach the narrative regardless of which path classification took."""
    snap = _investigated(conn, "My landlord is refusing to return my security "
                               "deposit after I moved out of the flat.")
    assert snap["case"]["problem_type"] == "general_consumer_problem"
    assert snap["research"]["sources"], "an unclassified case retrieved nothing"


def test_research_recovers_from_a_misclassification(conn):
    """Retrieval searches the narrative, not the label, so a wrong label cannot
    steer it. This case classifies as an airline problem; the guidance it needs
    is housing."""
    snap = _investigated(conn, "The builder has not handed over possession of my "
                               "flat three years after the agreed date.")
    sectors = {s["sector"] for s in snap["research"]["sources"]}
    assert sectors == {"housing"}, (
        f"expected housing guidance regardless of the label, got {sectors}")


def test_a_case_outside_the_corpus_records_no_research(conn):
    snap = _investigated(conn, "My hotel in Paris cancelled my booking on "
                               "arrival and I had to pay more elsewhere.")
    assert snap["research"]["sources"] == []


def test_migration_upgrades_a_database_that_predates_the_rank_column(tmp_path):
    """The upgrade path every existing deployment takes, which no other test can see.

    Every test here opens a fresh database, so `CREATE TABLE IF NOT EXISTS` always
    creates the current schema and the suite is structurally blind to the case
    where the table already exists in an older shape. That case is not
    hypothetical — it is what happens to every database that ran a previous
    version of this migration, and it surfaced only when the app was run against
    a real one, as a 500 on case creation.
    """
    import sqlite3
    from agentx import store

    db = tmp_path / "legacy.db"
    legacy = sqlite3.connect(db)
    legacy.execute(
        "CREATE TABLE case_research (id TEXT PRIMARY KEY, workspace TEXT, "
        "case_id TEXT, passage_id TEXT, sector TEXT, title TEXT, authority TEXT, "
        "citation TEXT, score REAL, coverage REAL, claim TEXT, verdict TEXT, "
        "because TEXT, created_at TEXT)")
    legacy.commit()
    legacy.close()

    store.reset_for_tests(str(db))
    store.ensure_schema()

    check = sqlite3.connect(db)
    columns = {r[1] for r in check.execute("PRAGMA table_info(case_research)")}
    check.close()
    assert "rank" in columns, (
        "a database created before `rank` existed was not upgraded; "
        "CREATE TABLE IF NOT EXISTS is a no-op against an existing table")


def test_research_does_not_alter_the_entitlement_analysis(conn):
    """The contract with the rest of the engine: research informs, never decides.

    Retrieval must leave the policy findings and remedy ranking byte-identical to
    what the deterministic analysis produced on its own.
    """
    from agentx import eligibility, research as research_mod
    snap = _investigated(conn, "My flight from Delhi was cancelled two hours "
                               "before departure and the airline refuses to pay.")
    case_id = snap["case"]["id"]
    before = (eligibility.load_policies(conn, case_id),
              eligibility.load(conn, case_id))
    # Re-run retrieval and persist a different result; the analysis must not move.
    research_mod.persist(conn, case_id, snap["case"]["workspace"], [])
    after = (eligibility.load_policies(conn, case_id),
             eligibility.load(conn, case_id))
    assert before == after
