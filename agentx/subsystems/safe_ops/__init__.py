"""
Safe Operations — detect a problem, prepare the fix, hold it for approval.

Vendored from a working operations workflow. `_service/` holds its steps in the
order the original ran them: ingest an alert, analyse it, wait for approval,
apply the approved fix, verify, and record what happened.

WHY IT RUNS AS A SERVICE

The original is a Node workflow runtime, not a Python library. Porting it would
mean rewriting the engine that makes it work — the opposite of preserving it. So
it runs as its own process and Agent X reaches it over HTTP, which keeps the real
implementation intact and keeps one product identity in front of it.

APPROVAL IS AGENT X'S, NOT THE SERVICE'S

The vendored workflow has its own wait-for-approval step. Agent X does not
delegate the decision to it: any action reaching outward is assessed by
`governor.assess()` first, so a second approval system cannot become a way around
the first. The service's step remains as its internal gate; Agent X's governor is
the one that answers to the user.
"""
from __future__ import annotations

import os

# The workflow's stages, from the vendored step files. Declared so the flow is
# describable while the service is not running.
STAGES = ("ingest alert", "analyse", "await approval", "apply fix",
          "health check", "record resolution")


def endpoint() -> str | None:
    return os.environ.get("AGENT_X_OPS_URL") or None


def available() -> dict:
    target = endpoint()
    return {
        "available": bool(target),
        "stages": list(STAGES),
        "endpoint_configured": bool(target),
        "detail": ("Connected to the operations workflow service."
                   if target else
                   "The operations workflow runs as its own service and none is "
                   "configured. Its steps are vendored here and readable; set "
                   "AGENT_X_OPS_URL to connect one."),
        "approval": "Any outward action is gated by Agent X's governor first.",
    }
