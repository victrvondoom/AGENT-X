"""Vulnerability triage over HTTP. Admin-facing, read-only.

Deliberately only reports state. The fleet's own FastAPI app (`server.py`)
exposes the endpoints that start a run, and mounting those here would put
repository-writing actions behind Agent X's HTTP surface without routing them
through the governor first. Until that wiring exists, this says what the track
is and whether it could run — which is what the registry needs to stop
claiming a route that 404s.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/agentx/sentinel_x", tags=["sentinel_x"])


@router.get("/status")
def status():
    """Whether the triage fleet can run here, and how far it could get."""
    from agentx.subsystems import sentinel_x
    state = sentinel_x.available()
    state["storage"] = sentinel_x.storage_backends()
    return state
