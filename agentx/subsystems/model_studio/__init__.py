"""
Model Studio — building a small model from your own data.

Vendored from a working training pipeline. `_pipeline/` is that code: dataset
preparation, a training configuration builder, the trainer itself, and an
evaluation stage that reports how the result actually performs.

TRAINING IS HARDWARE-BOUND AND THIS TRACK SAYS SO

The pipeline needs a GPU and a heavyweight inference/training stack. On a
machine without them this track reports unavailable and explains why. It does
NOT offer a visual editor that pretends to train — a studio whose train button
produces a fabricated accuracy figure is worse than no studio, because someone
will believe the number.

What remains available without the stack is inspection: the pipeline's stages
and the configuration it would use are readable, so the workflow can be
understood before committing hardware to it.
"""
from __future__ import annotations

import os

# Stages the vendored pipeline runs, in order. Declared here so the workflow is
# describable even when the training stack is absent.
STAGES = ("dataset", "preprocess", "configure", "train", "evaluate", "export")


def _stack_present() -> tuple[bool, str]:
    try:
        import oumi  # noqa: F401
        return True, ""
    except Exception as exc:
        return False, str(exc)


def available() -> dict:
    """Whether a model can actually be trained here."""
    present, why = _stack_present()
    if not present:
        return {
            "available": False,
            "can_inspect": True,
            "stages": list(STAGES),
            "detail": ("The training stack is not installed on this machine, so "
                       "no model can be trained here. The pipeline's stages and "
                       "configuration remain readable. Point AGENT_X_STUDIO_URL "
                       "at a machine that has it to run training there."),
            "reason": why,
        }
    return {"available": True, "can_inspect": True, "stages": list(STAGES),
            "detail": "The training stack is present; training can run locally."}


def remote() -> str | None:
    """A machine configured to run training on this deployment's behalf."""
    return os.environ.get("AGENT_X_STUDIO_URL") or None
