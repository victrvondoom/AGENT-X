"""
Learning Memory's HTTP surface, mounted into Agent X.

The original ran as its own FastAPI application on its own port. Here the same
handlers hang off an Agent X router, which is the whole integration: one process,
one origin, one auth rule, one place a person looks.

WHAT CHANGED FROM THE ORIGINAL, AND WHY

    prefix      routes move under /api/agentx/learning so they sit beside the
                rest of Agent X rather than colliding with it — the original's
                `/api/health` would have shadowed Agent X's own.
    auth        writes are token-gated by Agent X's `require_auth`. The original
                had no auth because it was a single-tenant demo; anything that
                mutates a learner's record has to be gated here.
    errors      a provider failure becomes a structured HTTP error rather than a
                stack trace, matching how the rest of this API answers.

The handler bodies are the original's, calling the same provider methods with
the same arguments. Nothing about how learning state is computed changed.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/agentx/learning", tags=["learning"])


def require_auth(authorization: str | None = Header(None)) -> None:
    """Agent X's bearer-token gate, resolved without importing the API module.

    `app.agentx_api` imports this package's router, so importing it back here at
    module scope would be circular. The rule it enforces is identical: when
    AGENT_X_AUTH_TOKEN is set, mutating routes require it.
    """
    token = os.environ.get("AGENT_X_AUTH_TOKEN")
    if token and authorization != f"Bearer {token}":
        raise HTTPException(401, "a bearer token is required to change a record")


def _provider():
    """Resolved per request so a track that cannot start reports 503, not 500."""
    from agentx.subsystems import learning
    state = learning.available()
    if not state["available"]:
        raise HTTPException(503, detail={"error": "learning_unavailable",
                                         "detail": state["detail"]})
    return learning.provider()


def _guard(fn, *args):
    """The original's error contract: a missing learner is a 404, not a crash."""
    try:
        return fn(*args)
    except KeyError as exc:
        raise HTTPException(404, f"not found: {exc}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


class StudentBody(BaseModel):
    student: str


class AnswerBody(BaseModel):
    student: str
    concept: str
    answer_index: int


class AskBody(BaseModel):
    student: str
    question: str


class ClassAskBody(BaseModel):
    question: str


class RetireBody(BaseModel):
    student: str
    concept: str


class ClassSetupBody(BaseModel):
    students: list[str]


class AssignReviewBody(BaseModel):
    concept: str


# ── reads ───────────────────────────────────────────────────────────────────
@router.get("/status")
def learning_status():
    from agentx.subsystems import learning
    return learning.available()


@router.get("/students")
def students():
    return _provider().students()


@router.get("/student/graph")
def student_graph(student: str, offset_days: int = 0):
    return _guard(_provider().student_graph, student, offset_days)


@router.get("/student/timeline")
def student_timeline(student: str, offset_days: int = 0):
    return _guard(_provider().student_timeline, student, offset_days)


@router.get("/student/report")
def student_report(student: str):
    return _guard(_provider().student_report, student)


@router.get("/class/heatmap")
def class_heatmap(offset_days: int = 0):
    return _provider().class_heatmap(offset_days)


@router.get("/teacher/plan")
def teaching_plan(offset_days: int = 0):
    return _provider().teaching_plan(offset_days)


@router.get("/curricula")
def curricula():
    return _provider().curricula()


@router.post("/quiz/next")
def quiz_next(body: StudentBody):
    return _guard(_provider().quiz_next, body.student)


@router.post("/quiz/answer")
def quiz_answer(body: AnswerBody):
    return _guard(_provider().quiz_answer, body.student, body.concept,
                  body.answer_index)


@router.post("/ask")
def ask(body: AskBody):
    return _guard(_provider().ask, body.student, body.question)


@router.post("/class/ask")
def class_ask(body: ClassAskBody):
    return _guard(_provider().class_ask, body.question)


# ── record-changing writes ──────────────────────────────────────────────────
# These alter a learner's record or the class roster. The original had no auth
# because it ran as a single-tenant demo on its own port; mounted into Agent X
# they carry the same bearer-token gate as every other mutating route, which is
# the one behavioural difference between this file and the original.
@router.post("/student/add", dependencies=[Depends(require_auth)])
def add_student(body: StudentBody):
    return _guard(_provider().add_student, body.student)


@router.post("/class/setup", dependencies=[Depends(require_auth)])
def class_setup(body: ClassSetupBody):
    return _guard(_provider().setup_class, body.students)


@router.post("/curriculum/import", dependencies=[Depends(require_auth)])
def import_curriculum(body: dict):
    return _guard(_provider().import_curriculum, body)


@router.post("/retire", dependencies=[Depends(require_auth)])
def retire(body: RetireBody):
    """Retire a concept for a learner — they have demonstrably mastered it."""
    return _guard(_provider().retire, body.student, body.concept)


@router.post("/reset-student", dependencies=[Depends(require_auth)])
def reset_student(body: StudentBody):
    """Wipe a learner's history. Destructive and not reversible."""
    return _guard(_provider().reset_student, body.student)


@router.post("/teacher/assign-review", dependencies=[Depends(require_auth)])
def assign_review(body: AssignReviewBody):
    return _guard(_provider().assign_review, body.concept)
