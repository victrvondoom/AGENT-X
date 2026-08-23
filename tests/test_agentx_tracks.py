"""
Capability tracks — the registry, and the honesty of its status.

One test here exists because of a real outage during development: a new package
`agentx/capabilities/` was added next to the long-standing module
`agentx/capabilities.py`, and the package shadowed the module. Five subsystems
import it, so 86 tests failed at once. It is trivially avoidable and completely
invisible until something imports the shadowed name, which is exactly the kind
of thing a test should hold.
"""
from __future__ import annotations

import pkgutil

import pytest

from agentx import tracks


# ─────────────────────────────────────────────── the registry
def test_every_track_is_describable_without_jargon():
    """A person reads this list to decide what to try. Names and summaries must
    stand on their own, and must never leak the codebase a capability came from."""
    for row in tracks.catalogue():
        assert row["name"].strip() and row["summary"].strip()
        assert row["summary"].endswith("."), row["name"]
        assert row["examples"], f"{row['name']} shows no example of its use"


def test_no_user_facing_text_names_a_source_project():
    """`origin` is for maintainers. It must not appear in what a user reads."""
    leaks = ("classroom", "dealdesk", "voicegraph", "opsguard", "infoundry",
             "vriksha", "dcrca", "agri-bot", "agribot", "sentinel", "proxy",
             "crewai", "tambo", "portia", "cognee", "oumi", "kestra", "motia")
    for row in tracks.catalogue():
        blob = " ".join([row["name"], row["summary"], *row["examples"]]).lower()
        for name in leaks:
            assert name not in blob, f"{row['id']} exposes {name!r} to users"


def test_track_ids_are_unique():
    assert len(tracks.CATALOGUE) == len(tracks._TRACKS)


def test_autonomy_is_one_of_the_declared_levels():
    allowed = {tracks.THINK, tracks.PREPARE, tracks.ACT, tracks.APPROVE}
    for track in tracks._TRACKS:
        assert track.autonomy in allowed, track.id


def test_a_track_declares_exactly_one_backing():
    """Either it runs here or it runs elsewhere. Both would make `status()`
    ambiguous; neither makes the track undiscoverable rather than honest."""
    for track in tracks._TRACKS:
        assert bool(track.module) != bool(track.service_env), (
            f"{track.id} declares {'both' if track.module else 'neither'} a "
            f"module and a service")


# ─────────────────────────────────────────────── status honesty
def test_status_is_resolved_not_stored():
    """A hardcoded 'live' survives the code it describes being deleted."""
    fake = tracks.Track(id="x", name="X", summary="Nothing.", examples=("e",),
                        module="agentx.this_module_does_not_exist")
    assert fake.status() == tracks.UNAVAILABLE
    assert fake.as_dict()["usable"] is False


def test_a_service_track_is_unavailable_without_its_env(monkeypatch):
    fake = tracks.Track(id="y", name="Y", summary="Nothing.", examples=("e",),
                        service_env="AGENT_X_TEST_SERVICE_URL")
    monkeypatch.delenv("AGENT_X_TEST_SERVICE_URL", raising=False)
    assert fake.status() == tracks.UNAVAILABLE
    monkeypatch.setenv("AGENT_X_TEST_SERVICE_URL", "http://example.invalid")
    assert fake.status() == tracks.SERVICE


def test_a_service_track_is_never_reported_usable(monkeypatch):
    """`service` means the code exists somewhere else, not that it works from
    here. Only an import in this process earns `usable`."""
    monkeypatch.setenv("AGENT_X_TEST_SERVICE_URL", "http://example.invalid")
    fake = tracks.Track(id="z", name="Z", summary="Nothing.", examples=("e",),
                        service_env="AGENT_X_TEST_SERVICE_URL")
    assert fake.as_dict()["usable"] is False


def test_the_shipped_tracks_are_actually_usable():
    """These are Agent X's own; if any is not importable, something is broken."""
    usable = {r["id"] for r in tracks.catalogue() if r["usable"]}
    for expected in ("consumer_resolution", "documents_evidence",
                     "knowledge_research", "voice_intake", "system_recovery"):
        assert expected in usable, f"{expected} should be live"


def test_summary_counts_match_the_catalogue():
    rows = tracks.catalogue()
    summary = tracks.summary()
    assert summary["tracks"] == len(rows)
    assert summary["usable_now"] == sum(1 for r in rows if r["usable"])


def test_usable_only_filters():
    assert all(r["usable"] for r in tracks.catalogue(usable_only=True))


def test_catalogue_order_is_stable():
    first = [r["id"] for r in tracks.catalogue()]
    for _ in range(3):
        assert [r["id"] for r in tracks.catalogue()] == first


# ─────────────────────────────────────────────── the shadowing outage
def test_no_subsystem_package_shadows_an_agentx_module():
    """The regression that broke 86 tests at once.

    A package under `agentx/` whose name matches an existing `agentx/<name>.py`
    silently wins the import, and every module importing the old name breaks.
    Nothing warns; it simply stops working.
    """
    import agentx
    root = agentx.__path__[0]
    modules, packages = set(), set()
    for info in pkgutil.iter_modules([root]):
        (packages if info.ispkg else modules).add(info.name)
    clash = modules & packages
    assert not clash, f"package(s) shadowing a module of the same name: {clash}"


def test_the_capability_registry_is_still_the_module_everyone_imports():
    """Five subsystems call `capabilities.get()`. Pinned directly."""
    from agentx import capabilities
    assert hasattr(capabilities, "get")
    assert capabilities.get("email_interaction") is not None


# ─────────────────────────────────────────────── HTTP surface
def test_tracks_endpoint_is_public_and_honest():
    from fastapi.testclient import TestClient
    from app.main import app

    data = TestClient(app).get("/api/agentx/tracks").json()
    assert data["usable_now"] == sum(1 for t in data["tracks"] if t["usable"])
    assert data["note"].strip()


def test_capabilities_live_inside_the_one_app():
    """There is no separate capabilities page any more — it is a view of /app.

    The standalone page was real scattering: a second URL, a second design
    system, and a second place to keep in step. The old path still resolves so
    existing links keep working, but it lands in the app rather than beside it.
    """
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    r = client.get("/capabilities", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/app#/capabilities"

    page = client.get("/app")
    assert page.status_code == 200
    assert 'data-view="capabilities"' in page.text
    assert "What Agent X can do" in page.text


def test_every_folded_route_targets_a_view_that_exists():
    """The defect this catches actually shipped: four routes redirected to
    `/app#/agents`, `#/activity`, `#/evidence` and `#/settings`, none of which
    were view names in /app. Every one silently dropped the user on Triage."""
    import re
    from fastapi.testclient import TestClient
    from app.main import app, _FOLDED

    html = TestClient(app).get("/app").text
    views = set(re.findall(r'<section data-view="([a-z]+)"', html))
    assert views, "no views found in /app"

    for path, target in _FOLDED.items():
        want = target.lstrip("#/")
        assert want in views, (
            f"{path} redirects to #{want}, which is not a view in /app")


def test_no_emoji_in_the_product_ui():
    """A product decision, asserted so it cannot drift back in."""
    import pathlib
    import re
    for name in ("index.html", "agentx.html", "landing.html"):
        path = pathlib.Path("templates") / name
        # Pictographs only. Arrows, box-drawing rules, checkmarks and the
        # sun/moon theme glyphs are typography — banning those would ban the
        # design system rather than the emoji.
        found = re.findall(r"[\U0001F300-\U0001FAFF⏩-⏺️]",
                           path.read_text(encoding="utf-8"))
        assert not found, f"{name} contains emoji: {sorted(set(found))}"


def test_no_dead_template_is_left_behind():
    """Every template must be reachable. Three were not, and 1,423 lines of
    abandoned UI sat in the tree looking maintained."""
    import pathlib
    import re

    main = pathlib.Path("app/main.py").read_text(encoding="utf-8")
    trustdoc = pathlib.Path("app/trustdoc.py").read_text(encoding="utf-8")
    served = set(re.findall(r'_serve\(\s*"([\w.]+)"', main))
    served |= set(re.findall(r'"templates",\s*"([\w.]+)"', trustdoc))
    # Saved and opened from disk rather than served by a route of its own.
    served |= {"verify_offline.html"}

    on_disk = {p.name for p in pathlib.Path("templates").glob("*.html")}
    orphans = on_disk - served
    assert not orphans, f"templates no route serves: {sorted(orphans)}"


# ─────────────────────────────────────────────── a vendored subsystem
def test_the_learning_subsystem_runs_offline():
    """Vendored from a working codebase and must need no credentials."""
    from agentx.subsystems import learning
    state = learning.available()
    assert state["available"] is True, state["detail"]
    assert state["mode"] == "demo"


def test_learning_routes_return_real_data():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    students = client.get("/api/agentx/learning/students").json()
    rows = students["students"] if isinstance(students, dict) else students
    assert rows, "the vendored ledger should be seeded"
    first = rows[0]["id"] if isinstance(rows[0], dict) else rows[0]

    report = client.get("/api/agentx/learning/student/report",
                        params={"student": first})
    assert report.status_code == 200


def test_an_unknown_learner_is_a_404_not_a_crash():
    from fastapi.testclient import TestClient
    from app.main import app

    r = TestClient(app).get("/api/agentx/learning/student/report",
                            params={"student": "nobody-at-all"})
    assert r.status_code == 404
