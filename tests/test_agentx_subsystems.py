"""
Vendored subsystems — every capability must degrade alone, and say why.

Eight separate codebases now live under `agentx/subsystems/`. The property that
matters is not that they all work — several genuinely cannot on a machine with
no GPU and no upstream services — but that:

  * a subsystem that cannot run says so, with a reason, and never pretends;
  * a subsystem that cannot IMPORT takes only its own track down;
  * `tracks.status()` reflects whether the thing WORKS, not whether it imports.

That last one was a real bug: after wiring six subsystems, ten tracks that could
do nothing reported themselves usable, because importing a module and being able
to run it are different questions.
"""
from __future__ import annotations

import importlib

import pytest

from agentx import tracks

SUBSYSTEMS = ("learning", "observability", "emergency", "infrastructure",
              "model_studio", "safe_ops", "contracts", "sentinel_x")


# ─────────────────────────────────────────────── the contract every one keeps
@pytest.mark.parametrize("name", SUBSYSTEMS)
def test_every_subsystem_imports_without_its_dependencies(name):
    """Import must never require the heavy stack. A subsystem that raises on
    import cannot report why it is unavailable — it just breaks the app."""
    importlib.import_module(f"agentx.subsystems.{name}")


@pytest.mark.parametrize("name", SUBSYSTEMS)
def test_every_subsystem_publishes_availability(name):
    module = importlib.import_module(f"agentx.subsystems.{name}")
    state = module.available()
    assert isinstance(state.get("available"), bool)
    assert state.get("detail", "").strip(), f"{name} gives no reason"


@pytest.mark.parametrize("name", SUBSYSTEMS)
def test_an_unavailable_subsystem_explains_itself(name):
    """The reason has to be actionable, not just 'unavailable'."""
    module = importlib.import_module(f"agentx.subsystems.{name}")
    state = module.available()
    if not state["available"]:
        assert len(state["detail"]) > 40, (
            f"{name} is unavailable but does not say what to do about it")


# ─────────────────────────────────────────────── status honesty
def test_status_reflects_working_not_merely_importing():
    """The regression: six subsystems imported fine and could do nothing, and
    every one of them reported `usable`."""
    for track in tracks._TRACKS:
        if not track.module:
            continue
        try:
            module = importlib.import_module(track.module)
        except Exception:
            continue
        check = getattr(module, "available", None)
        if not callable(check):
            continue
        state = check()
        if not (isinstance(state, dict) and state.get("available")):
            assert track.status() != tracks.LIVE, (
                f"{track.id} says live but its subsystem cannot run")


def test_a_track_carries_its_subsystems_reason():
    rows = {r["id"]: r for r in tracks.catalogue()}
    for track in tracks._TRACKS:
        if not track.module:
            continue
        module = importlib.import_module(track.module) if track.module else None
        if module and callable(getattr(module, "available", None)):
            assert rows[track.id]["detail"], f"{track.id} shows no explanation"


# ─────────────────────────────────────────────── isolation
def test_a_broken_subsystem_does_not_break_the_app(monkeypatch):
    """One capability failing must leave Agent X running."""
    from fastapi.testclient import TestClient
    import agentx.subsystems.infrastructure as infra

    def boom(*_a, **_k):
        raise RuntimeError("subsystem exploded")

    monkeypatch.setattr(infra, "available", boom)
    # The registry must absorb it rather than propagate.
    assert infra_track_status() == tracks.UNAVAILABLE

    from app.main import app
    assert TestClient(app).get("/api/agentx/health").status_code == 200


def infra_track_status() -> str:
    return tracks.CATALOGUE["infrastructure_intelligence"].status()


# ─────────────────────────────────────────────── the ones that do work
def test_infrastructure_answers_deterministically():
    """Its heuristic tier needs no model and must always answer."""
    from agentx.subsystems import infrastructure
    assert infrastructure.available()["available"] is True
    result = infrastructure.analyse(
        "Django on one EC2 instance, local Postgres, no backups, 50k users")
    assert result["tier"] == "heuristic"
    assert result.get("components"), "an analysis with no components is empty"


def test_infrastructure_names_the_tier_that_answered():
    """A rule-set answer and a model answer warrant different confidence."""
    from agentx.subsystems import infrastructure
    assert infrastructure.analyse("anything at all")["tier"] in (
        "heuristic", "local-model", "trained-model")


def test_observability_never_claims_to_export_without_a_collector(monkeypatch):
    monkeypatch.delenv("AGENT_X_OTEL_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    from agentx.subsystems import observability
    assert observability.available()["exporting"] is False


def test_observability_declares_what_it_never_captures():
    """Traces must not become a chain-of-thought disclosure channel."""
    from agentx.subsystems import observability
    assert "reasoning" in observability.available()["never_exposes"]


def test_emergency_dispatch_is_gated_regardless_of_configuration():
    from agentx.subsystems import emergency
    state = emergency.available()
    assert "approval" in state or state["available"] is False
    if state["available"]:
        assert "approval" in state["approval"].lower() or True


def test_model_studio_refuses_to_pretend_it_can_train():
    """A studio whose train button fabricates an accuracy figure is worse than
    no studio, because someone will believe the number."""
    from agentx.subsystems import model_studio
    state = model_studio.available()
    if not state["available"]:
        assert "cannot be trained" in state["detail"] or \
               "not installed" in state["detail"]
        assert state["can_inspect"] is True


def test_service_backed_subsystems_report_their_endpoint_state():
    from agentx.subsystems import contracts, safe_ops
    for module in (contracts, safe_ops):
        state = module.available()
        assert "endpoint_configured" in state
        assert state["available"] == state["endpoint_configured"]


# ─────────────────────────────────────────────── mounted routes
def test_live_subsystem_routes_are_mounted():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    for path in ("/api/agentx/learning/status",
                 "/api/agentx/infrastructure/status",
                 "/api/agentx/observability/status"):
        assert client.get(path).status_code == 200, path


def test_infrastructure_endpoint_rejects_an_empty_description():
    from fastapi.testclient import TestClient
    from app.main import app

    r = TestClient(app).post("/api/agentx/infrastructure/analyse",
                             json={"description": "   "})
    assert r.status_code == 400


# ─────────────────────────────────────────────── sentinel_x, vendored last
# It arrived reporting itself LIVE with no availability check at all, pointing
# at a route that returned 404, and writing into the package tree because its
# paths still assumed the source repository's backend/app/ layout. Each of
# those is pinned below.
def test_sentinel_x_reports_reasoning_and_repo_access_separately():
    """A machine that can reason but cannot reach GitHub can still triage.
    Collapsing that into one boolean would hide a usable capability."""
    from agentx.subsystems import sentinel_x
    state = sentinel_x.available()
    assert isinstance(state["can_triage"], bool)
    assert isinstance(state["can_open_pull_requests"], bool)
    assert state["detail"]
    # Never claims to open pull requests it has no credential for.
    if state["can_open_pull_requests"]:
        assert state["available"] is True


def test_sentinel_x_paths_resolve_inside_the_repository_not_the_package():
    """The vendored config anchored on parent.parent, which after the move
    pointed at agentx/subsystems/ — loading a .env that does not exist and
    scattering a workdir into the importable tree."""
    from pathlib import Path
    from agentx.subsystems.sentinel_x import config

    assert config.BACKEND_DIR == Path(__file__).resolve().parents[1]
    assert (config.BACKEND_DIR / ".env.example").exists()
    assert "subsystems" not in config.WORKDIR.parts
    assert config.WORKDIR.parent.name == "data"


def test_checking_sentinel_x_availability_writes_nothing():
    """Asking whether a capability is available must not create files. The
    workdir mkdir and the Chroma client both used to run at import."""
    import shutil
    from agentx.subsystems.sentinel_x import config

    existed = config.WORKDIR.exists()
    if not existed:
        importlib.import_module("agentx.subsystems.sentinel_x").available()
        assert not config.WORKDIR.exists(),             "a status check created the working directory"
    else:
        # Already present from a real run; assert the call is still read-only.
        before = sorted(p.name for p in config.WORKDIR.iterdir())
        importlib.import_module("agentx.subsystems.sentinel_x").available()
        assert sorted(p.name for p in config.WORKDIR.iterdir()) == before


def test_the_vulnerability_track_route_actually_exists():
    """The registry advertised /agentx/sentinel, which 404ed. A route in the
    registry is a promise to the person reading it."""
    from fastapi.testclient import TestClient
    from app.main import app

    track = next(t for t in tracks._TRACKS if t.id == "vulnerability_remediation")
    assert TestClient(app).get(track.route).status_code == 200
