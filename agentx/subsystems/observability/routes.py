"""Agent Observability over HTTP. Admin-facing, read-only."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/agentx/observability", tags=["observability"])


@router.get("/status")
def status():
    """What is recorded, where it goes, and what is deliberately never captured."""
    from agentx.subsystems import observability
    return observability.available()
