"""Configuration must not depend on which directory the process started in.

python-dotenv searches upward from the current working directory. Started
from the repo root rather than backend/, that search found nothing: no
Gemini key, no GCP project, and the store and queue silently falling back
to their local filesystem backends. Nothing raised - it simply was not the
system anyone believed they were running, which is the worst kind of
configuration bug.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent


def _probe(cwd: Path) -> dict:
    """Import app.config in a fresh interpreter from `cwd` and report it."""
    code = (
        "import sys, os, json;"
        f"sys.path.insert(0, {str(BACKEND_DIR)!r});"
        "from app import config;"
        "print(json.dumps({"
        "'model': config.GEMINI_MODEL,"
        "'backend_dir': str(config.BACKEND_DIR),"
        # These come only from .env - a value with a code default (like the
        # model) matches whether or not .env loaded, which is exactly how an
        # earlier version of this test passed against the broken behaviour.
        "'has_key': bool(config.GEMINI_API_KEY),"
        "'project': config.GCP_PROJECT_ID,"
        "}))"
    )
    # Scrub the values .env is supposed to provide. Subprocesses inherit
    # this process's environment, and pytest has already imported app.config
    # (which loads .env), so without this the child sees the keys via
    # inheritance no matter what and the test passes against broken code -
    # which is exactly what an earlier version of it did.
    env = dict(os.environ)
    for var in ("GEMINI_API_KEY", "GCP_PROJECT_ID", "GOOGLE_APPLICATION_CREDENTIALS",
                "SENTINEL_STORE_BACKEND", "SENTINEL_QUEUE_BACKEND", "NUTRIENT_API_KEY"):
        env.pop(var, None)

    out = subprocess.run(
        [sys.executable, "-c", code], cwd=cwd, capture_output=True, text=True, timeout=180, env=env
    )
    assert out.returncode == 0, out.stderr[-800:]
    import json

    return json.loads(out.stdout.strip().splitlines()[-1])


def test_config_resolves_the_same_from_backend_and_repo_root():
    """The whole point: cwd must not change what the process is configured
    as. Asserted on values that exist only in .env, because anything with a
    code default looks identical whether or not .env was ever found."""
    import pytest

    if not (BACKEND_DIR / ".env").exists():
        pytest.skip("no .env present (CI); nothing to load either way")

    from_backend = _probe(BACKEND_DIR)
    from_root = _probe(REPO_ROOT)

    assert from_backend["backend_dir"] == from_root["backend_dir"]
    assert from_backend["has_key"] == from_root["has_key"], (
        "the API key is visible from one directory and not the other - "
        "the process comes up silently unconfigured depending on cwd"
    )
    assert from_backend["project"] == from_root["project"]


def test_backend_dir_points_at_the_backend_package():
    from app import config

    assert (config.BACKEND_DIR / "app" / "config.py").exists()


def test_relative_credentials_are_made_absolute(monkeypatch, tmp_path):
    """Google's client libraries resolve this against cwd, so the natural
    "gcp-key.json" in .env only works from inside backend/."""
    from app import config

    key = config.BACKEND_DIR / "_probe_key.json"
    key.write_text("{}", encoding="utf-8")
    try:
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "_probe_key.json")
        config._absolutise_credentials()
        resolved = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
        assert Path(resolved).is_absolute()
        assert Path(resolved).exists()
    finally:
        key.unlink(missing_ok=True)


def test_an_absolute_credential_path_is_left_alone(monkeypatch, tmp_path):
    from app import config

    real = tmp_path / "key.json"
    real.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(real))
    config._absolutise_credentials()
    assert os.environ["GOOGLE_APPLICATION_CREDENTIALS"] == str(real)


def test_a_missing_relative_credential_is_not_rewritten(monkeypatch):
    """Rewriting a path that does not exist would only make the eventual
    error message point somewhere misleading."""
    from app import config

    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "definitely-not-here.json")
    config._absolutise_credentials()
    assert os.environ["GOOGLE_APPLICATION_CREDENTIALS"] == "definitely-not-here.json"
