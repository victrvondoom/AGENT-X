"""Infrastructure Intelligence over HTTP, mounted into Agent X."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/agentx/infrastructure", tags=["infrastructure"])


class AnalyseReq(BaseModel):
    description: str


@router.get("/status")
def status():
    from agentx.subsystems import infrastructure
    return infrastructure.available()


@router.post("/analyse")
def analyse(r: AnalyseReq):
    """Analyse a deployment description. Public: it reads nothing of the user's.

    The response always names the tier that answered, because a deterministic
    rule-set answer and a model-backed one warrant different confidence and the
    caller is entitled to know which they got.
    """
    if not r.description.strip():
        raise HTTPException(400, "describe the deployment to analyse")
    from agentx.subsystems import infrastructure
    state = infrastructure.available()
    if not state["available"]:
        raise HTTPException(503, detail={"error": "infrastructure_unavailable",
                                         "detail": state["detail"]})
    return infrastructure.analyse(r.description)
