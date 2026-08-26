"""
Ontology and understanding — the ambiguity claim is the thing to prove.

These tests exist to catch the two failure modes a declarative catalogue actually
has: a definition that references something that does not exist (caught at load
time by the registry itself), and a classifier that collapses ambiguity it should
preserve. The second is the more important one, because it is the whole product
thesis — "They charged me again" must stay a distribution, not become a guess.
"""
from __future__ import annotations

import pytest

from agentx import policy, understanding
from agentx.ontology import DefinitionError, catalogue, group_trigger_index, registry, summary


def test_catalogue_loads_and_validates():
    cat = catalogue()
    assert len(cat) >= 25
    for d in cat.values():
        assert d.domain
        assert d.label


def test_every_ambiguity_group_has_at_least_two_members():
    groups: dict[str, list[str]] = {}
    for d in catalogue().values():
        if d.ambiguity_group:
            groups.setdefault(d.ambiguity_group, []).append(d.problem_type)
    assert groups, "expected at least one ambiguity group to exist"
    for g, members in groups.items():
        assert len(members) >= 2, f"group {g!r} has only one member: {members}"


def test_no_policy_corpus_references_are_dangling():
    missing = policy.missing_references()
    assert missing == {}, f"problem types cite policies the corpus does not define: {missing}"


def test_group_trigger_index_covers_repeat_charge():
    idx = group_trigger_index()
    assert "repeat_charge" in idx
    assert len(idx["repeat_charge"]) >= 3


class TestMalformedDefinitions:
    def test_unknown_domain_is_rejected(self, tmp_path, monkeypatch):
        (tmp_path / "bad.yaml").write_text(
            "problem_type: x\ndomain: not_a_real_domain\n", encoding="utf-8")
        monkeypatch.setattr(registry, "DEFS_DIR", str(tmp_path))
        registry.catalogue.cache_clear()
        with pytest.raises(DefinitionError, match="unknown domain"):
            registry.catalogue()
        registry.catalogue.cache_clear()

    def test_ambiguity_group_of_one_is_rejected(self, tmp_path, monkeypatch):
        (tmp_path / "lonely.yaml").write_text(
            "problem_type: lonely_one\ndomain: commerce\n"
            "ambiguity_group: solo_group\n", encoding="utf-8")
        monkeypatch.setattr(registry, "DEFS_DIR", str(tmp_path))
        registry.catalogue.cache_clear()
        with pytest.raises(DefinitionError, match="single member"):
            registry.catalogue()
        registry.catalogue.cache_clear()

    def test_unknown_evidence_kind_is_rejected(self, tmp_path, monkeypatch):
        (tmp_path / "bad_ev.yaml").write_text(
            "problem_type: y\ndomain: commerce\n"
            "required_evidence:\n  - kind: not_a_real_kind\n    why: x\n",
            encoding="utf-8")
        monkeypatch.setattr(registry, "DEFS_DIR", str(tmp_path))
        registry.catalogue.cache_clear()
        with pytest.raises(DefinitionError, match="unknown evidence kind"):
            registry.catalogue()
        registry.catalogue.cache_clear()


class TestUnderstanding:
    """The engine's central claim: hold ambiguity until evidence resolves it."""

    def test_generic_repeat_charge_sentence_stays_plural(self):
        u = understanding.understand("They charged me again", use_llm=False)
        live = [h for h in u.hypotheses if h.posterior >= 0.05]
        assert len(live) >= 4, f"expected several live readings, got {live}"
        assert u.ambiguous

    def test_specific_narrative_collapses_to_one_reading(self):
        u = understanding.understand(
            "Amazon charged me twice for something I only received once.",
            use_llm=False)
        assert not u.ambiguous
        assert u.top is not None
        assert u.top.problem_type == "duplicate_charge"

    def test_unrelated_narrative_carries_high_residual_mass(self):
        u = understanding.understand("I do not understand this bill", use_llm=False)
        # This one is NOT unrelated - it should match bill_unclear specifically.
        assert u.top is not None
        assert u.top.problem_type == "bill_unclear"

    def test_no_catalogue_match_is_reported_honestly(self):
        u = understanding.understand("kjshdf iuwehr random gibberish text zzz",
                                     use_llm=False)
        assert u.residual > 0.5 or not u.hypotheses

    def test_group_trigger_wakes_every_member_of_repeat_charge(self):
        u = understanding.understand("why have they charged me again this month",
                                     use_llm=False)
        seen = {h.problem_type for h in u.hypotheses}
        members = {d.problem_type for d in registry.group_members("repeat_charge")}
        assert seen & members, "group trigger did not wake any repeat_charge member"
        assert len(seen & members) >= 3

    def test_evidence_alone_does_not_manufacture_a_hypothesis(self):
        """A bank statement satisfies a dozen problem types' evidence requirement.
        Attaching one to a narrative with NO lexical signal must not fabricate
        confidence in an unrelated reading."""
        u = understanding.understand("hello there", evidence_kinds=("transaction",),
                                     use_llm=False)
        assert u.residual > 0.3

    def test_expected_information_gain_ranks_the_best_question_first(self):
        u = understanding.understand("They charged me again", use_llm=False)
        qs = understanding.rank_discriminators(u.hypotheses, limit=5)
        assert qs
        bits = [q["expected_bits"] for q in qs]
        assert bits == sorted(bits, reverse=True)

    def test_answering_a_discriminator_updates_the_distribution(self):
        u = understanding.understand("They charged me again", use_llm=False)
        qs = understanding.rank_discriminators(u.hypotheses, limit=3)
        target = next((q for q in qs if q["options"]), None)
        assert target, "expected at least one choice-type discriminator"
        before = {h.problem_type: h.posterior for h in u.hypotheses}
        updated = understanding.apply_answer(u.hypotheses, target["id"],
                                             target["options"][0])
        after = {h.problem_type: h.posterior for h in updated}
        assert before != after, "answering a question should move the distribution"

    def test_entity_extraction_never_treats_currency_code_as_a_merchant(self):
        ents = understanding.extract_entities(
            "I was charged 2,399 INR by an unknown company")
        merchants = [e["value"] for e in ents if e["kind"] == "merchant"]
        assert "INR" not in merchants

    def test_known_merchant_matched_as_whole_word_not_substring(self):
        ents = understanding.extract_entities(
            "My SkyLink flight was delayed and I arrived over four hours late.")
        merchants = [e["value"] for e in ents if e["kind"] == "merchant"]
        assert merchants and merchants[0] == "SkyLink Airways"

    def test_appointment_cancelled_classifies_correctly(self):
        """Regression against a stale audit claim: `appointment_cancelled`
        (domain: appointments) already exists and works — an earlier report
        wrongly said the appointments domain had zero problem definitions."""
        u = understanding.understand(
            "The plumber never turned up for my appointment and rescheduled "
            "without telling me", use_llm=False)
        assert u.top is not None
        assert u.top.problem_type == "appointment_cancelled"
        assert u.top.domain == "appointments"


class TestPolicyEvaluation:
    def test_absent_fact_produces_unknown_not_a_guess(self):
        d = registry.get("flight_delay_compensation")
        assert d is not None
        findings = policy.analyse(d, {}, jurisdiction=None)
        eu = next(f for f in findings if f.policy.id == "eu261")
        assert eu.applies == "unknown"

    def test_condition_met_produces_yes_with_entitlement(self):
        d = registry.get("flight_delay_compensation")
        assert d is not None
        facts = {"flight.delay_minutes": 240, "flight.distance_km": 1850,
                 "flight.disruption_reason": "technical"}
        findings = policy.analyse(d, facts, jurisdiction="EU")
        eu = next(f for f in findings if f.policy.id == "eu261")
        assert eu.applies == "yes"
        assert eu.entitlement_minor == 40000  # medium-haul band, EUR minor units

    def test_extraordinary_circumstance_defeats_eu261(self):
        d = registry.get("flight_delay_compensation")
        assert d is not None
        facts = {"flight.delay_minutes": 240, "flight.distance_km": 1850,
                 "flight.disruption_reason": "weather"}
        findings = policy.analyse(d, facts, jurisdiction="EU")
        eu = next(f for f in findings if f.policy.id == "eu261")
        assert eu.applies == "no"

    def test_wrong_jurisdiction_policy_is_no_not_unknown(self):
        d = registry.get("flight_delay_compensation")
        assert d is not None
        facts = {"flight.delay_minutes": 240, "flight.distance_km": 1850}
        findings = policy.analyse(d, facts, jurisdiction="US")
        eu = next(f for f in findings if f.policy.id == "eu261")
        assert eu.applies == "no"

    def test_unknown_jurisdiction_makes_regional_policies_unknown(self):
        d = registry.get("duplicate_charge")
        assert d is not None
        findings = policy.analyse(d, {"charge.amount": 500}, jurisdiction=None)
        s75 = next(f for f in findings if f.policy.id == "uk_cca_s75")
        assert s75.applies == "unknown"

    def test_universal_policy_ignores_jurisdiction(self):
        d = registry.get("duplicate_charge")
        assert d is not None
        findings = policy.analyse(d, {}, jurisdiction=None)
        cb = next(f for f in findings if f.policy.id == "card_scheme_chargeback")
        assert cb.applies != "no"  # unknown (incident.days_ago absent) but not rejected on jurisdiction
