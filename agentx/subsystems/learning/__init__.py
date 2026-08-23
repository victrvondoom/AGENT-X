"""
Learning Memory — what someone has learned, what they have not, and what next.

The engineering here is preserved from a working classroom-memory system: a
learner ledger, mastery bands, spaced review, quiz selection driven by weakness,
and class-level aggregation. `providers.py` and `ledger.py` are that system,
vendored with only their import boundary changed.

TWO PROVIDERS, AND THE OFFLINE ONE IS THE DEFAULT

    demo    a complete implementation over a local SQLite ledger. No network,
            no credentials, no external service. Everything works.
    cloud   the same interface backed by a hosted knowledge service, selected
            only when its credentials are present.

That split came with the original code and is the reason this track can be
`live` on a laptop with nothing configured. `CLASSROOM_MODE` selects; absent, it
is `demo`.

WHERE STATE LIVES

The original wrote its ledger beside its own source file. Here it goes to Agent
X's data directory, so one product has one place it keeps things and a
capability cannot scatter databases through the source tree. That is a boundary
change, not a behavioural one.
"""
from __future__ import annotations

import os
from pathlib import Path

# Agent X's data directory — the same one the case engine's SQLite file uses.
DATA_DIR = Path(__file__).resolve().parents[3] / "data"

_provider = None


def mode() -> str:
    """`demo` unless a deployment explicitly asks for cloud."""
    return (os.environ.get("CLASSROOM_MODE") or "demo").strip().lower() or "demo"


def provider():
    """The learning provider, built once.

    Built lazily rather than at import so that a misconfigured cloud mode
    surfaces when the track is used, not when Agent X boots.
    """
    global _provider
    if _provider is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        # The vendored provider resolves its ledger path relative to its own
        # file. Pointing it at Agent X's data directory keeps one product's
        # state in one place.
        os.environ.setdefault("CLASSROOM_DB", str(DATA_DIR / "learning.db"))
        from agentx.subsystems.learning.providers import make_provider
        _provider = make_provider(mode())
    return _provider


def available() -> dict:
    """Whether this track can run, and in which mode — for /tracks and health."""
    current = mode()
    if current == "cloud" and not os.environ.get("COGNEE_CLOUD_API_KEY"):
        return {"available": False, "mode": current,
                "detail": "Cloud mode is selected but its credentials are not "
                          "configured. Set CLASSROOM_MODE=demo to run offline."}
    try:
        provider()
    except Exception as exc:
        return {"available": False, "mode": current,
                "detail": f"The learning provider could not start: {exc}"}
    return {"available": True, "mode": current,
            "detail": ("Running offline against a local ledger; no external "
                       "service is involved." if current == "demo"
                       else "Running against the configured knowledge service.")}
