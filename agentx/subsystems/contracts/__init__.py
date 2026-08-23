"""
Contract Intelligence — turning a contract into something you can act on.

Vendored from a working generative-UI contract application. `_service/` is that
application: the clause analysis, the risk model, and the components that render
an answer as an interactive artefact — a risk map, an obligation timeline, a
renewal schedule — rather than as a paragraph of prose.

WHY IT RUNS AS A SERVICE

It is a Next.js application. Its whole contribution is the generated interface,
which cannot be reproduced by a Python template without rebuilding the thing that
makes it worth having. So it runs as its own process behind Agent X, and the
answer a user sees is the real artefact the original produces.

WHAT AGENT X ADDS

Documents already uploaded to a case are the natural input, so a contract does
not have to be supplied twice. Where a contract bears on a dispute, the two
tracks compose: the clause analysis becomes evidence in the case rather than a
separate reading exercise.
"""
from __future__ import annotations

import os

# The artefact kinds the vendored application can generate.
ARTEFACTS = ("risk map", "clause checklist", "obligation timeline",
             "renewal timeline", "payment risk breakdown", "responsibility map")


def endpoint() -> str | None:
    return os.environ.get("AGENT_X_CONTRACT_URL") or None


def available() -> dict:
    target = endpoint()
    return {
        "available": bool(target),
        "artefacts": list(ARTEFACTS),
        "endpoint_configured": bool(target),
        "detail": ("Connected to the contract analysis service."
                   if target else
                   "Contract analysis runs as its own service and none is "
                   "configured. Its implementation is vendored here; set "
                   "AGENT_X_CONTRACT_URL to connect one."),
    }
