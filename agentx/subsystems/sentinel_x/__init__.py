"""Vulnerability triage and remediation — the SENTINEL AppSec fleet, vendored.

WHAT THIS IS

A fleet of agents that finds a vulnerable dependency in a repository, decides
whether the vulnerable code is actually reachable, drafts the upgrade, proves
the fix in a sandbox, and files the evidence. It arrived as a standalone
service (its own FastAPI app, worker and Next.js UI) and is vendored here whole
rather than rewritten, per the vendor-don't-rebuild rule.

WHAT IT NEEDS TO ACTUALLY RUN

Every agent that reasons — Analyst, Patch Forge, the ADK fleet, the Strands
orchestrator — calls Gemini over the network. There is no offline tier and no
canned-response fallback, by the design of the vendored code: without a key it
does not degrade, it raises. Hunting and patching also clone and branch real
repositories, which needs `git` on PATH and a GitHub token for anything
private or for opening a pull request.

So this module reports honestly, and reports the two capabilities separately:
a machine that can reason but cannot reach GitHub can still triage a local
checkout, and saying only "unavailable" would hide that.

WHY `available()` EXISTS AT ALL

`agentx.tracks` resolves a track's status by importing this module. Absent this
function it would fall through to a bare import check and report the track
LIVE — which it would be, in the sense that the bytes are here and importable,
and would not be, in the sense that a user asking it to fix a vulnerability
gets an exception. The registry's contract is that a track claiming to work
must work; this function is how that claim is kept truthful.
"""
from __future__ import annotations

import os
import shutil

# Read through config rather than os.environ directly: config is what loads the
# .env file, so importing it first is what makes these values present at all.
from agentx.subsystems.sentinel_x.config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GITHUB_TOKEN,
    WORKDIR,
)

# The stages a finding moves through, in order. Readable whether or not the
# fleet can run, so the pipeline is inspectable on an unconfigured machine.
STAGES = (
    "hunt",           # find vulnerable dependencies in the target repository
    "analyse",        # decide whether the advisory genuinely applies
    "reachability",   # is the vulnerable code path actually reachable
    "patch",          # draft the upgrade
    "verify",         # prove the patch in a sandbox
    "re-verify",      # confirm the finding is gone after the patch
    "evidence",       # seal what happened
)


def _git_present() -> bool:
    return shutil.which("git") is not None


def available() -> dict:
    """Whether this track can actually triage and remediate, and how far.

    Reported as two separate capabilities because they fail independently.
    """
    reasoning = bool(GEMINI_API_KEY)
    git = _git_present()
    github = bool(GITHUB_TOKEN)

    blockers: list[str] = []
    if not reasoning:
        blockers.append(
            "GEMINI_API_KEY is not set, so no agent in the fleet can reason. "
            "Every analysis and patch step calls Gemini and there is no "
            "offline tier"
        )
    if not git:
        blockers.append(
            "`git` is not on PATH, so no repository can be cloned or branched"
        )

    if blockers:
        return {
            "available": False,
            "can_triage": False,
            "can_open_pull_requests": False,
            "stages": list(STAGES),
            "model": GEMINI_MODEL,
            "workdir": str(WORKDIR),
            # Not a missing service on another host — it is missing local
            # configuration, so `endpoint_configured` stays absent and the
            # registry reports this UNAVAILABLE rather than SERVICE.
            "detail": "This track cannot run here: " + "; ".join(blockers) + ".",
        }

    return {
        "available": True,
        "can_triage": True,
        "can_open_pull_requests": github,
        "stages": list(STAGES),
        "model": GEMINI_MODEL,
        "workdir": str(WORKDIR),
        "detail": (
            "The triage fleet can run: a model key is configured and git is "
            "present. " + (
                "A GitHub token is configured, so fixes can be pushed and "
                "pull requests opened."
                if github else
                "No GITHUB_TOKEN is set, so it can analyse and patch a local "
                "checkout but cannot push a branch or open a pull request."
            )
        ),
        "governed": (
            "Any outward action — pushing a branch, opening a pull request — "
            "is gated by Agent X's governor before it happens."
        ),
    }


def storage_backends() -> dict:
    """Which store and queue this subsystem would use, for diagnostics."""
    return {
        "store": os.environ.get("SENTINEL_STORE_BACKEND", "local").lower(),
        "queue": os.environ.get("SENTINEL_QUEUE_BACKEND", "local").lower(),
    }
