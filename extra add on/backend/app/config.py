"""Environment-driven configuration. No secret ever gets hardcoded here -
every value below is read from the process environment (populated locally
via a .env file that is gitignored, and via real secret bindings once
deployed to Cloud Run)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
WORKDIR = BACKEND_DIR / "workdir"
WORKDIR.mkdir(exist_ok=True)

# Load .env by absolute path rather than letting python-dotenv search up
# from the current directory. Started from the repo root instead of
# backend/, the search found nothing and the whole process came up silently
# unconfigured - no Gemini key, no GCP project, store and queue quietly
# falling back to the local filesystem backends. Nothing errored; it just
# was not the system anyone thought they were running.
load_dotenv(BACKEND_DIR / ".env")


def _absolutise_credentials() -> None:
    """Make a relative GOOGLE_APPLICATION_CREDENTIALS work from any cwd.

    The Google client libraries resolve this path against the current
    working directory, so the natural "gcp-key.json" in .env only works
    when the process happens to start inside backend/.
    """
    creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if creds and not Path(creds).is_absolute():
        resolved = (BACKEND_DIR / creds).resolve()
        if resolved.exists():
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(resolved)


_absolutise_credentials()

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# The repo Hunter scans for this MVP - a real, publicly disclosed-vulnerable
# app, not a fabricated finding. See backend build prompt "Demo data".
DEMO_REPO_URL = os.environ.get("DEMO_REPO_URL", "https://github.com/juice-shop/juice-shop.git")
DEMO_REPO_DIR = WORKDIR / "juice-shop"

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")

# The single source of truth for which Gemini model the fleet reasons with.
# The All Things Agentic rules require Gemini 3.5 or newer, so that is the
# floor, not a preference - every LLM-backed agent (Analyst, Patch Forge,
# the ADK fleet, the Strands orchestrator) resolves its model from here so
# the version can never drift apart across call sites.
#
# Default is gemini-3.6-flash rather than 3.5: quota on this project is
# per-model, and gemini-3.5-flash was exhausted by real investigation runs
# while 3.6 still had headroom on the same key. 3.6 is newer than 3.5, so
# the requirement is satisfied either way - this is purely about which one
# will actually answer during a demo.
#
# If 3.6 is ever exhausted too, set GEMINI_MODEL to any other Gemini 3.5+
# model with capacity. Do NOT drop to 2.5: it answers, but it silently puts
# the project out of compliance with the rules.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
