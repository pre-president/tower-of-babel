"""Chat Bridge API endpoints (exclusively authorized via Bridge Token)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_bridge
from app.config import Actor
from app.db import get_db
from app.models import Decision
from app.schemas import (
    DecisionApproveRequest,
    DecisionOut,
    MessagesSyncRequest,
    MessagesSyncResponse,
    QuestionOut,
    QuestionResolveRequest,
    ThreadOut,
    ThreadRegisterRequest,
)
from app.services.decisions import approve_or_supersede_decision, reject_decision
from app.services.dialogues import sync_messages
from app.services.questions import list_pending_questions, resolve_question
from app.services.threads import list_threads, upsert_planner_thread

router = APIRouter(prefix="/api/v1/bridge", tags=["bridge"])


class DecisionRejectRequest(BaseModel):
    reason: str = ""


class ThreadInboxOut(BaseModel):
    planner_thread_id: str
    pending_questions: list[QuestionOut]
    proposed_decisions: list[DecisionOut]


@router.post("/threads/register", response_model=ThreadOut)
def register_thread(
    req: ThreadRegisterRequest,
    actor: Actor = Depends(require_bridge),
    db: Session = Depends(get_db),
) -> ThreadOut:
    return upsert_planner_thread(db, actor, req)  # type: ignore[return-value]


@router.get("/threads", response_model=list[ThreadOut])
def get_registered_threads(
    project_id: str | None = Query(default=None),
    _: Actor = Depends(require_bridge),
    db: Session = Depends(get_db),
) -> list[ThreadOut]:
    return list_threads(db, project_id=project_id)  # type: ignore[return-value]


@router.post("/messages/sync", response_model=MessagesSyncResponse)
def sync_chat_messages(
    req: MessagesSyncRequest,
    actor: Actor = Depends(require_bridge),
    db: Session = Depends(get_db),
) -> MessagesSyncResponse:
    return sync_messages(db, actor, req)


@router.post("/decisions/publish", response_model=DecisionOut)
def publish_decision(
    req: DecisionApproveRequest,
    actor: Actor = Depends(require_bridge),
    db: Session = Depends(get_db),
) -> DecisionOut:
    return approve_or_supersede_decision(db, actor, req)  # type: ignore[return-value]


@router.post("/decisions/{decision_id}/reject", response_model=DecisionOut)
def reject_proposed_decision(
    decision_id: str,
    req: DecisionRejectRequest,
    actor: Actor = Depends(require_bridge),
    db: Session = Depends(get_db),
) -> DecisionOut:
    return reject_decision(db, actor, decision_id, req.reason)  # type: ignore[return-value]


@router.get("/threads/{planner_thread_id}/inbox", response_model=ThreadInboxOut)
def get_thread_inbox(
    planner_thread_id: str,
    _: Actor = Depends(require_bridge),
    db: Session = Depends(get_db),
) -> ThreadInboxOut:
    questions = list_pending_questions(db, planner_thread_id=planner_thread_id)
    proposed = list(
        db.scalars(
            select(Decision)
            .where(
                Decision.planner_thread_id == planner_thread_id,
                Decision.status == "PROPOSED",
            )
            .order_by(Decision.created_at.asc())
        ).all()
    )
    return ThreadInboxOut(
        planner_thread_id=planner_thread_id,
        pending_questions=questions,  # type: ignore[arg-type]
        proposed_decisions=proposed,  # type: ignore[arg-type]
    )


@router.post("/questions/{question_id}/resolve", response_model=QuestionOut)
def resolve_thread_question(
    question_id: str,
    req: QuestionResolveRequest,
    actor: Actor = Depends(require_bridge),
    db: Session = Depends(get_db),
) -> QuestionOut:
    return resolve_question(db, actor, question_id, req)  # type: ignore[return-value]
