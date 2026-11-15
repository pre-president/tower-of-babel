"""Question queue routing between execution nodes and designated ChatGPT planner threads."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Actor
from app.models import ExecutionTask, PlannerThread, Question, utc_now
from app.schemas import QuestionResolveRequest, QuestionSubmitRequest
from app.services.audit import record_audit


def submit_question(db: Session, actor: Actor, req: QuestionSubmitRequest) -> Question:
    """Submit a clarification question; routes ambiguous threads to NEEDS_TRIAGE."""
    task: ExecutionTask | None = None
    target_thread_id = req.planner_thread_id

    if req.task_id:
        task = db.get(ExecutionTask, req.task_id)
        if task is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
        if not target_thread_id:
            target_thread_id = task.planner_thread_id

    initial_status = "PENDING"
    thread: PlannerThread | None = None
    if target_thread_id:
        thread = db.get(PlannerThread, target_thread_id)
        if thread is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Planner thread '{target_thread_id}' not found",
            )
    else:
        initial_status = "NEEDS_TRIAGE"

    q = Question(
        task_id=req.task_id,
        planner_thread_id=target_thread_id,
        asked_by_agent=actor.actor_id,
        question_text=req.question_text,
        evidence_context=req.evidence_context,
        impact_note=req.impact_note,
        status=initial_status,
    )
    db.add(q)
    db.flush()

    if thread is not None:
        thread.pending_question_count += 1
    if task is not None and req.block_task and task.status in ("EXECUTING", "RESUMED"):
        task.status = "WAITING_FOR_DECISION"

    record_audit(
        db,
        actor=actor,
        action="question_submitted",
        target_type="question",
        target_id=q.question_id,
        detail={"planner_thread_id": target_thread_id, "status": initial_status},
    )
    db.commit()
    db.refresh(q)
    return q


def list_pending_questions(
    db: Session, planner_thread_id: str | None = None
) -> list[Question]:
    """List unresolved questions, optionally scoped to one planner thread."""
    stmt = (
        select(Question)
        .where(Question.status.in_(["PENDING", "IN_CHAT", "NEEDS_TRIAGE"]))
        .order_by(Question.created_at.asc())
    )
    if planner_thread_id:
        stmt = stmt.where(Question.planner_thread_id == planner_thread_id)
    return list(db.scalars(stmt).all())


def resolve_question(
    db: Session, actor: Actor, question_id: str, req: QuestionResolveRequest
) -> Question:
    """Resolve a question with ChatGPT's answer (Bridge only) and optionally unblock its task."""
    q = db.scalar(select(Question).where(Question.question_id == question_id).with_for_update())
    if q is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")
    if q.status == "RESOLVED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Question is already resolved",
        )

    q.status = "RESOLVED"
    q.answer_text = req.answer_text
    q.resolution_message_id = req.resolution_message_id
    q.resolved_at = utc_now()

    if q.planner_thread_id:
        thread = db.get(PlannerThread, q.planner_thread_id)
        if thread is not None and thread.pending_question_count > 0:
            thread.pending_question_count -= 1

    if req.resume_task and q.task_id:
        task = db.get(ExecutionTask, q.task_id)
        if task is not None and task.status == "WAITING_FOR_DECISION":
            task.status = "EXECUTING" if task.lease_owner else "PLANNED"

    record_audit(
        db,
        actor=actor,
        action="question_resolved",
        target_type="question",
        target_id=q.question_id,
        detail={"task_id": q.task_id},
    )
    db.commit()
    db.refresh(q)
    return q


def get_question_by_id(db: Session, question_id: str) -> Question:
    """Fetch a single question record by ID."""
    q = db.get(Question, question_id)
    if q is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")
    return q


def mark_question_in_chat(
    db: Session, actor: Actor, question_id: str, *, planner_thread_id: str | None = None
) -> Question:
    """Mark a pending/triage question IN_CHAT (and optionally assign its thread)."""
    q = db.scalar(select(Question).where(Question.question_id == question_id).with_for_update())
    if q is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")
    if q.status == "RESOLVED":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Question is already resolved")

    if planner_thread_id and not q.planner_thread_id:
        thread = db.get(PlannerThread, planner_thread_id)
        if thread is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Planner thread '{planner_thread_id}' not found",
            )
        q.planner_thread_id = planner_thread_id
        thread.pending_question_count += 1

    q.status = "IN_CHAT"
    record_audit(
        db,
        actor=actor,
        action="question_marked_in_chat",
        target_type="question",
        target_id=q.question_id,
        detail={"planner_thread_id": q.planner_thread_id},
    )
    db.commit()
    db.refresh(q)
    return q
