"""
Infrastructure Intelligence — reading how something is deployed, and what to fix.

Vendored from a working cloud-architecture agent. Its useful property, and the
reason this track can be live with nothing configured, is that the original was
built with a three-tier fallback already in place:

    fine-tuned model   if one is trained and reachable
    local model host   if one is running
    heuristic          a deterministic rule set that always answers

The heuristic tier is real analysis, not a placeholder — it is the path the
original used when no model was available, and it is what runs here by default.
Nothing in this module fabricates a recommendation it cannot justify.

`_brain/` is the original source, unmodified. This file is the boundary: it
imports the analysis functions directly rather than running the vendored FastAPI
app, because Agent X already has an app and does not need a second one.
"""
from __future__ import annotations

import os


def _analysis_module():
    """The vendored analysis module, imported lazily.

    NOT named `_brain`: importing the `_brain` subpackage binds that name as an
    attribute of this package and would silently overwrite a function called the
    same thing. Same shadowing trap as a package colliding with a module.

    Lazy because `_brain/serve.py` builds a FastAPI app at import time; deferring
    that keeps Agent X's startup free of a second application object, and keeps
    an import failure inside this track instead of taking the process down.
    """
    import importlib
    return importlib.import_module("agentx.subsystems.infrastructure._brain.serve")


def tier() -> str:
    """Which analysis tier will actually answer, resolved from configuration."""
    if os.environ.get("USE_OUMI") == "1":
        return "trained-model"
    if os.environ.get("USE_OLLAMA") == "1" and os.environ.get("OLLAMA_URL"):
        return "local-model"
    return "heuristic"


def available() -> dict:
    """Whether this track can answer, and how good the answer will be.

    Always available: the heuristic tier needs no model, no key and no network.
    What changes with configuration is the QUALITY of the answer, and that is
    reported rather than hidden.
    """
    try:
        _analysis_module()
    except Exception as exc:
        return {"available": False, "tier": None,
                "detail": f"The analysis module could not be loaded: {exc}"}
    current = tier()
    detail = {
        "heuristic": "Answering from a deterministic rule set. Reliable and "
                     "explainable, but narrower than a model-backed answer. "
                     "Configure a local model host for deeper analysis.",
        "local-model": "Answering via the configured local model host.",
        "trained-model": "Answering via the fine-tuned architecture model.",
    }[current]
    return {"available": True, "tier": current, "detail": detail}


def analyse(prompt: str) -> dict:
    """Analyse a deployment description and return recommendations.

    Routes through the vendored tiers in the original's order, falling back the
    way the original falls back. A tier that fails is skipped, never faked.
    """
    brain = _analysis_module()
    current = tier()

    if current == "trained-model":
        try:
            answer = brain.call_oumi(prompt)
            if answer:
                return {"tier": current, "analysis": answer}
        except Exception:
            pass
    if current in ("trained-model", "local-model"):
        try:
            answer = brain.call_ollama(prompt)
            if answer:
                return {"tier": "local-model", "analysis": answer}
        except Exception:
            pass

    # The deterministic tier. Always answers, and says that is what it is.
    return {"tier": "heuristic", **brain.heuristic_response(prompt)}
