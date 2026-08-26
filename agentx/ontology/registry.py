"""
The problem-type registry — loads the declarative catalogue and validates it.

Loading is the easy half. The half that matters is validation, and it is strict on
purpose: a malformed definition is caught at import time with a message naming the
file and the field, rather than at 2am when a case routes to a remedy that has no
provider behind it.

Every cross-reference is checked against `types.py`: domains, entity kinds,
evidence kinds, remedy kinds, risk levels. A definition that cites an evidence
kind nobody can produce, or a remedy nobody can execute, is a definition that will
strand a real user's case halfway through — so it fails to load instead.

The registry is a process-wide singleton because the catalogue is data on disk and
never changes at runtime. `reload()` exists for tests and for editing definitions
without restarting the server.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache

import yaml

from .types import (DOMAINS, ENTITY_KINDS, EVIDENCE_KINDS, REMEDY_KINDS, RISK_LEVELS,
                    DeadlineRule, Discriminator, EvidenceRequirement, ProblemDefinition)

DEFS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "definitions")


class DefinitionError(ValueError):
    """A problem definition is malformed. Names the file and the offending field."""


def _require(cond: object, path: str, msg: str) -> None:
    if not cond:
        raise DefinitionError(f"{os.path.basename(path)}: {msg}")


def _parse_one(raw: dict, path: str) -> ProblemDefinition:
    _require(isinstance(raw, dict), path, "top level must be a mapping")
    pt = raw.get("problem_type")
    _require(bool(pt), path, "missing `problem_type`")
    assert pt is not None
    _require(re.fullmatch(r"[a-z][a-z0-9_]*", pt or ""), path,
             f"problem_type {pt!r} must be lower_snake_case")

    domain = raw.get("domain")
    _require(domain in DOMAINS, path,
             f"unknown domain {domain!r} — must be one of {sorted(DOMAINS)}")
    assert domain is not None

    ev: list[EvidenceRequirement] = []
    for e in raw.get("required_evidence") or []:
        kind = e.get("kind")
        _require(kind in EVIDENCE_KINDS, path, f"unknown evidence kind {kind!r}")
        alts = tuple(e.get("satisfied_by") or ())
        for a in alts:
            _require(a in EVIDENCE_KINDS, path, f"unknown evidence kind {a!r} in satisfied_by")
        ev.append(EvidenceRequirement(kind=kind, why=e.get("why", ""),
                                      critical=bool(e.get("critical", True)),
                                      satisfied_by=alts))

    disc: list[Discriminator] = []
    for d in raw.get("discriminators") or []:
        _require(bool(d.get("id")) and bool(d.get("question")), path,
                 "each discriminator needs an `id` and a `question`")
        disc.append(Discriminator(
            id=d["id"], question=d["question"], kind=d.get("kind", "choice"),
            options=tuple(d.get("options") or ()),
            favours=dict(d.get("favours") or {}),
            disfavours=dict(d.get("disfavours") or {}),
            why=d.get("why", "")))

    dls: list[DeadlineRule] = []
    for x in raw.get("deadlines") or []:
        _require("days" in x and "label" in x, path,
                 "each deadline needs `label` and `days`")
        dls.append(DeadlineRule(label=x["label"], days=int(x["days"]),
                                kind=x.get("kind", "scheme"),
                                from_event=x.get("from_event", "incident"),
                                source=x.get("source", "")))

    for k in (raw.get("required_entities") or []) + (raw.get("optional_entities") or []):
        _require(k in ENTITY_KINDS, path, f"unknown entity kind {k!r}")
    for r in raw.get("resolution_strategies") or []:
        _require(r in REMEDY_KINDS, path, f"unknown remedy kind {r!r}")

    risk = raw.get("risk", "medium")
    _require(risk in RISK_LEVELS, path, f"unknown risk {risk!r}")
    autonomy = int(raw.get("default_autonomy", 2))
    _require(0 <= autonomy <= 4, path, f"default_autonomy {autonomy} outside 0..4")
    prior = float(raw.get("prior", 0.05))
    _require(0.0 < prior <= 1.0, path, f"prior {prior} must be in (0, 1]")

    return ProblemDefinition(
        problem_type=pt, domain=domain,
        label=raw.get("label") or pt.replace("_", " ").title(),
        summary=raw.get("summary", ""),
        prior=prior,
        ambiguity_group=raw.get("ambiguity_group"),
        phrases=tuple(raw.get("phrases") or ()),
        patterns=tuple(raw.get("patterns") or ()),
        negative_phrases=tuple(raw.get("negative_phrases") or ()),
        group_triggers=tuple(raw.get("group_triggers") or ()),
        required_entities=tuple(raw.get("required_entities") or ()),
        optional_entities=tuple(raw.get("optional_entities") or ()),
        required_evidence=tuple(ev),
        expected_facts=tuple(raw.get("expected_facts") or ()),
        discriminators=tuple(disc),
        policies=tuple(raw.get("policies") or ()),
        resolution_strategies=tuple(raw.get("resolution_strategies") or ()),
        escalation=tuple(raw.get("escalation") or ()),
        deadlines=tuple(dls),
        risk=risk, default_autonomy=autonomy,
        provider_family=raw.get("provider_family"),
    )


@lru_cache(maxsize=1)
def catalogue() -> dict[str, ProblemDefinition]:
    """Every problem type, keyed by name. Loaded once, validated eagerly."""
    out: dict[str, ProblemDefinition] = {}
    if not os.path.isdir(DEFS_DIR):
        return out
    for name in sorted(os.listdir(DEFS_DIR)):
        if not name.endswith((".yaml", ".yml")):
            continue
        path = os.path.join(DEFS_DIR, name)
        with open(path, encoding="utf-8") as f:
            docs = [d for d in yaml.safe_load_all(f) if d]
        for raw in docs:
            d = _parse_one(raw, path)
            if d.problem_type in out:
                raise DefinitionError(
                    f"{name}: duplicate problem_type {d.problem_type!r} — a problem "
                    f"type must have exactly one definition or routing is ambiguous")
            out[d.problem_type] = d
    _check_ambiguity_groups(out)
    return out


def _check_ambiguity_groups(cat: dict[str, ProblemDefinition]) -> None:
    """A group of one is a bug, not a group.

    Ambiguity groups exist so that a phrase matching several problem types produces
    several live hypotheses. A group with a single member means someone declared an
    ambiguity and then only wrote one side of it, which silently disables the
    multi-hypothesis path for that phrase.
    """
    groups: dict[str, list[str]] = {}
    for d in cat.values():
        if d.ambiguity_group:
            groups.setdefault(d.ambiguity_group, []).append(d.problem_type)
    lonely = {g: m for g, m in groups.items() if len(m) < 2}
    if lonely:
        raise DefinitionError(
            f"ambiguity groups with a single member: {lonely}. An ambiguity group "
            f"must contain at least two rival problem types.")


def reload() -> dict[str, ProblemDefinition]:
    catalogue.cache_clear()
    group_trigger_index.cache_clear()
    return catalogue()


def get(problem_type: str) -> ProblemDefinition | None:
    return catalogue().get(problem_type)


def by_domain(domain: str) -> list[ProblemDefinition]:
    return [d for d in catalogue().values() if d.domain == domain]


def group_members(group: str) -> list[ProblemDefinition]:
    return [d for d in catalogue().values() if d.ambiguity_group == group]


@lru_cache(maxsize=1)
def group_trigger_index() -> dict[str, tuple[str, ...]]:
    """ambiguity group -> the regexes that should wake ALL of its members.

    A phrase like "they charged me again" is evidence that the case belongs to a
    group, and no evidence at all about which member. Matching it must therefore
    put every rival on the table rather than the one whose keyword list happened
    to be written first — which, without this, is exactly what would happen.
    """
    out: dict[str, list[str]] = {}
    for d in catalogue().values():
        if d.ambiguity_group and d.group_triggers:
            out.setdefault(d.ambiguity_group, []).extend(d.group_triggers)
    return {g: tuple(dict.fromkeys(v)) for g, v in out.items()}


def summary() -> dict:
    """Catalogue statistics — surfaced at /api/agentx/ontology so the breadth of the
    engine is inspectable rather than asserted in a README."""
    cat = catalogue()
    doms: dict[str, int] = {}
    for d in cat.values():
        doms[d.domain] = doms.get(d.domain, 0) + 1
    groups: dict[str, list[str]] = {}
    for d in cat.values():
        if d.ambiguity_group:
            groups.setdefault(d.ambiguity_group, []).append(d.problem_type)
    return {
        "problem_types": len(cat),
        "domains": doms,
        "ambiguity_groups": groups,
        "remedy_kinds": sorted(REMEDY_KINDS),
        "evidence_kinds": sorted(EVIDENCE_KINDS),
    }
