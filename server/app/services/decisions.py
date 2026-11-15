"""Versioned Decision Archive operations (PROPOSED -> APPROVED -> SUPERSEDED)."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Actor
from app.models import Decision, PlannerThread
from app.schemas import DecisionApproveRequest, DecisionProposeRequest
from app.services.audit import record_audit


def _ensure_thread(db: Session, planner_thread_id: str) -> PlannerThread:
    thread = db.get(PlannerThread, planner_thread_id)
    if thread is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Planner thread '{planner_thread_id}' not found",
        )
    return thread


def propose_decision(db: Session, actor: Actor, req: DecisionProposeRequest) -> Decision:
    """Create a PROPOSED decision (allowed for both Agents and Bridge)."""
    _ensure_thread(db, req.planner_thread_id)
    version = 1
    if req.supersedes_decision_id:
        old = db.get(Decision, req.supersedes_decision_id)
        if old is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Target decision '{req.supersedes_decision_id}' to supersede not found",
            )
        version = old.decision_version + 1

    dec = Decision(
        project_id=req.project_id,
        planner_thread_id=req.planner_thread_id,
        title=req.title,
        decision_content=req.decision_content,
        rationale=req.rationale,
        rejected_alternatives=req.rejected_alternatives,
        source_message_ids=req.source_message_ids,
        status="PROPOSED",
        supersedes_decision_id=req.supersedes_decision_id,
        decision_version=version,
        proposed_by=actor.actor_id,
    )
    db.add(dec)
    db.flush()
    record_audit(
        db,
        actor=actor,
        action="decision_proposed",
        target_type="decision",
        target_id=dec.decision_id,
        detail={"title": dec.title, "supersedes": dec.supersedes_decision_id},
    )
    db.commit()
    db.refresh(dec)
    return dec


def approve_or_supersede_decision(
    db: Session, actor: Actor, req: DecisionApproveRequest
) -> Decision:
    """Approve a PROPOSED decision or publish a new APPROVED decision, superseding old version."""
    if req.decision_id:
        dec = db.scalar(
            select(Decision).where(Decision.decision_id == req.decision_id).with_for_update()
        )
        if dec is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Decision '{req.decision_id}' not found",
            )
        if dec.status not in ("PROPOSED", "APPROVED"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot approve decision in status '{dec.status}'",
            )
        if req.decision_content:
            dec.decision_content = req.decision_content
        if req.rationale is not None:
            dec.rationale = req.rationale
        if req.supersedes_decision_id and not dec.supersedes_decision_id:
            dec.supersedes_decision_id = req.supersedes_decision_id
    else:
        if not (req.project_id and req.planner_thread_id and req.title and req.decision_content):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Must provide decision_id or full decision fields (project_id, planner_thread_id, title, decision_content)",
            )
        _ensure_thread(db, req.planner_thread_id)
        dec = Decision(
            project_id=req.project_id,
            planner_thread_id=req.planner_thread_id,
            title=req.title,
            decision_content=req.decision_content,
            rationale=req.rationale or "",
            rejected_alternatives=req.rejected_alternatives or "",
            source_message_ids=req.source_message_ids or [],
            supersedes_decision_id=req.supersedes_decision_id,
            proposed_by=actor.actor_id,
        )
        db.add(dec)
        db.flush()

    if dec.supersedes_decision_id:
        old = db.scalar(
            select(Decision)
            .where(Decision.decision_id == dec.supersedes_decision_id)
            .with_for_update()
        )
        if old is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Superseded decision '{dec.supersedes_decision_id}' not found",
            )
        if old.decision_id == dec.decision_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A decision cannot supersede itself",
            )
        if old.status in ("APPROVED", "PROPOSED"):
            old.status = "SUPERSEDED"
        dec.decision_version = old.decision_version + 1

    dec.status = "APPROVED"
    dec.approved_by = actor.actor_id

    record_audit(
        db,
        actor=actor,
        action="decision_approved",
        target_type="decision",
        target_id=dec.decision_id,
        detail={
            "version": dec.decision_version,
            "superseded_id": dec.supersedes_decision_id,
        },
    )
    db.commit()
    db.refresh(dec)
    return dec


def reject_decision(db: Session, actor: Actor, decision_id: str, reason: str = "") -> Decision:
    """Reject a PROPOSED decision (Bridge only)."""
    dec = db.get(Decision, decision_id)
    if dec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Decision not found")
    if dec.status != "PROPOSED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only PROPOSED decisions can be rejected (current: {dec.status})",
        )
    dec.status = "REJECTED"
    record_audit(
        db,
        actor=actor,
        action="decision_rejected",
        target_type="decision",
        target_id=dec.decision_id,
        detail={"reason": reason},
    )
    db.commit()
    db.refresh(dec)
    return dec


def list_decisions(
    db: Session,
    *,
    project_id: str | None = None,
    planner_thread_id: str | None = None,
    active_only: bool = True,
) -> tuple[list[Decision], list[dict]]:
    """Return active APPROVED (or all) decisions plus notices of superseded predecessors."""
    stmt = select(Decision).order_by(Decision.updated_at.desc())
    if project_id:
        stmt = stmt.where(Decision.project_id == project_id)
    if planner_thread_id:
        stmt = stmt.where(Decision.planner_thread_id == planner_thread_id)
    if active_only:
        stmt = stmt.where(Decision.status == "APPROVED")

    rows = list(db.scalars(stmt).all())
    notices = [
        {
            "superseded_decision_id": d.supersedes_decision_id,
            "replaced_by_decision_id": d.decision_id,
            "new_title": d.title,
            "new_version": d.decision_version,
        }
        for d in rows
        if d.status == "APPROVED" and d.supersedes_decision_id
    ]
    return rows, notices


def get_decision_by_id(db: Session, decision_id: str) -> Decision:
    """Return a single decision record by ID."""
    dec = db.get(Decision, decision_id)
    if dec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Decision '{decision_id}' not found",
        )
    return dec
