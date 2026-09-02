"""npm audit hard-requires a lockfile and refuses without one - discovered
when a from-scratch container hung on its first scan because the demo
repo's own .gitignore excludes package-lock.json. Any real target repo can
legitimately not commit one, so this has to be a general safeguard, not a
one-off fix for that repo.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.agents import hunter


def test_lockfile_is_generated_when_missing(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        hunter.subprocess, "run", lambda *a, **k: calls.append(a[0]) or MagicMock()
    )
    hunter._ensure_lockfile(tmp_path)
    assert len(calls) == 1
    assert "--package-lock-only" in calls[0]
    # Running the target repo's own lifecycle scripts (a full frontend
    # build, in the demo repo's case) is slow, may need tooling that is not
    # present, and has nothing to do with auditing dependencies.
    assert "--ignore-scripts" in calls[0]


def test_lockfile_generation_is_skipped_when_one_already_exists(tmp_path, monkeypatch):
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    called = []
    monkeypatch.setattr(hunter.subprocess, "run", lambda *a, **k: called.append(1))
    hunter._ensure_lockfile(tmp_path)
    assert called == [], "must not touch npm when a lockfile is already present"


def test_a_failed_regeneration_surfaces_through_the_real_audit_error(tmp_path, monkeypatch):
    """If npm cannot produce a lockfile, the audit call right after it will
    fail with npm's own clear error - that is deliberately the only error
    surfaced, rather than swallowing or duplicating it here."""
    monkeypatch.setattr(hunter.subprocess, "run", lambda *a, **k: MagicMock(returncode=1))
    hunter._ensure_lockfile(tmp_path)  # must not raise on its own
    assert not (tmp_path / "package-lock.json").exists()


def test_run_npm_audit_calls_the_lockfile_guard_first(tmp_path, monkeypatch):
    """The guard has to actually run on the real path, not just work when
    called directly - a unit test of _ensure_lockfile alone would not have
    caught the call site being missing."""
    order = []
    monkeypatch.setattr(hunter, "_ensure_lockfile", lambda d: order.append(("lockfile", d)))

    class _Result:
        returncode = 1
        stdout = '{"vulnerabilities": {}}'
        stderr = ""

    monkeypatch.setattr(hunter.subprocess, "run", lambda *a, **k: order.append(("audit",)) or _Result())
    hunter.run_npm_audit(tmp_path)
    assert order == [("lockfile", tmp_path), ("audit",)]
