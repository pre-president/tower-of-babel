"""Planner Thread Registry operations and flexible chat_ref resolution."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Actor
from app.models import PlannerThread
from app.schemas import ThreadRegisterRequest
from app.services.audit import record_audit


def upsert_planner_thread(db: Session, actor: Actor, req: ThreadRegisterRequest) -> PlannerThread:
    """Register or update a whitelisted ChatGPT planning window."""
    thread = db.get(PlannerThread, req.planner_thread_id)
    if thread is None:
        thread = PlannerThread(
            planner_thread_id=req.planner_thread_id,
            project_id=req.project_id,
            title=req.title,
            role_description=req.role_description,
            chatgpt_conversation_id=req.chatgpt_conversation_id,
            chatgpt_url=req.chatgpt_url,
            sync_enabled=req.sync_enabled,
            auto_send_enabled=req.auto_send_enabled,
        )
        db.add(thread)
        action = "thread_registered"
    else:
        thread.project_id = req.project_id
        thread.title = req.title
        thread.role_description = req.role_description
        thread.chatgpt_conversation_id = req.chatgpt_conversation_id
        thread.chatgpt_url = req.chatgpt_url
        thread.sync_enabled = req.sync_enabled
        thread.auto_send_enabled = req.auto_send_enabled
        action = "thread_updated"

    record_audit(
        db,
        actor=actor,
        action=action,
        target_type="planner_thread",
        target_id=thread.planner_thread_id,
        detail={"sync_enabled": thread.sync_enabled, "auto_send_enabled": thread.auto_send_enabled},
    )
    db.commit()
    db.refresh(thread)
    return thread


def resolve_chat_thread(db: Session, chat_ref: str) -> PlannerThread:
    """Resolve a planner thread by exact ID, ChatGPT UUID, exact title, or unique title substring."""
    ref = chat_ref.strip()
    if not ref:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Empty chat reference")

    by_id = db.get(PlannerThread, ref)
    if by_id is not None:
        return by_id

    by_conv = db.scalar(
        select(PlannerThread).where(PlannerThread.chatgpt_conversation_id == ref)
    )
    if by_conv is not None:
        return by_conv

    by_exact_title = db.scalars(select(PlannerThread).where(PlannerThread.title == ref)).all()
    if len(by_exact_title) == 1:
        return by_exact_title[0]

    by_partial_title = db.scalars(
        select(PlannerThread).where(PlannerThread.title.ilike(f"%{ref}%"))
    ).all()
    if len(by_partial_title) == 1:
        return by_partial_title[0]
    if len(by_partial_title) > 1:
        matches = [t.planner_thread_id for t in by_partial_title]
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ambiguous chat_ref '{chat_ref}' matches multiple threads: {matches}",
        )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Planner thread not found for chat_ref '{chat_ref}'",
    )


def list_threads(db: Session, project_id: str | None = None) -> list[PlannerThread]:
    """List registered planner threads, optionally filtered by project."""
    stmt = select(PlannerThread).order_by(PlannerThread.updated_at.desc())
    if project_id:
        stmt = stmt.where(PlannerThread.project_id == project_id)
    return list(db.scalars(stmt).all())
