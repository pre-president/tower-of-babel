"""Execution task lifecycle, lease locking, checkpointing, and model handoff service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Actor, get_settings
from app.models import ExecutionTask, PlannerThread, TaskCheckpoint, utc_now
from app.schemas import (
    ConfirmHandoffRequest,
    HandoffPackageOut,
    ProjectContextOut,
    StartWorkFromChatRequest,
    TaskClaimRequest,
    TaskCreateRequest,
    TaskHandoffRequest,
    TaskProgressRequest,
)
from app.services.audit import record_audit
from app.services.decisions import list_decisions
from app.services.dialogues import get_recent_messages, get_thread_digests
from app.services.threads import resolve_chat_thread

VALID_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "PLANNED": {"EXECUTING", "HANDOFF_PENDING", "FAILED"},
    "EXECUTING": {
        "WAITING_FOR_DECISION",
        "BLOCKED_QUOTA",
        "HANDOFF_PENDING",
        "COMPLETED",
        "FAILED",
    },
    "RESUMED": {
        "EXECUTING",
        "WAITING_FOR_DECISION",
        "BLOCKED_QUOTA",
        "HANDOFF_PENDING",
        "COMPLETED",
        "FAILED",
    },
    "WAITING_FOR_DECISION": {"EXECUTING", "RESUMED", "HANDOFF_PENDING", "FAILED"},
    "BLOCKED_QUOTA": {"HANDED_OFF", "HANDOFF_PENDING", "FAILED"},
    "HANDOFF_PENDING": {"HANDED_OFF", "FAILED"},
    "HANDED_OFF": {"RESUMED", "HANDOFF_PENDING", "FAILED"},
    "COMPLETED": set(),
    "FAILED": {"PLANNED", "HANDOFF_PENDING"},
}


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _lock_task(db: Session, task_id: str) -> ExecutionTask:
    task = db.scalar(
        select(ExecutionTask).where(ExecutionTask.task_id == task_id).with_for_update()
    )
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


def _assert_lease_holder(task: ExecutionTask, actor: Actor, session_id: str) -> None:
    if actor.role == "bridge":
        return
    if task.lease_owner != actor.actor_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Task '{task.task_id}' lease is held by '{task.lease_owner}', not '{actor.actor_id}'",
        )
    if task.lease_session_id and task.lease_session_id != session_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Session ID does not match active lease session",
        )


def _transition(task: ExecutionTask, new_status: str) -> None:
    if task.status == new_status:
        return
    allowed = VALID_STATUS_TRANSITIONS.get(task.status, set())
    if new_status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Invalid task status transition: {task.status} -> {new_status}",
        )
    task.status = new_status


def create_task(db: Session, actor: Actor, req: TaskCreateRequest) -> ExecutionTask:
    """Create a new task in PLANNED status linked to a registered planner thread."""
    if db.get(PlannerThread, req.planner_thread_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Planner thread '{req.planner_thread_id}' not found",
        )
    task = ExecutionTask(
        project_id=req.project_id,
        planner_thread_id=req.planner_thread_id,
        title=req.title,
        goal_description=req.goal_description,
        acceptance_criteria=req.acceptance_criteria,
        local_repo_path=req.local_repo_path,
        git_branch=req.git_branch,
        worktree_path=req.worktree_path,
        status="PLANNED",
    )
    db.add(task)
    db.flush()
    record_audit(
        db,
        actor=actor,
        action="task_created",
        target_type="task",
        target_id=task.task_id,
        detail={"planner_thread_id": task.planner_thread_id, "title": task.title},
    )
    db.commit()
    db.refresh(task)
    return task


def build_project_context(
    db: Session, thread: PlannerThread, chat_turn_limit: int = 20
) -> ProjectContextOut:
    """Assemble Tier-1 fresh-start context for a planner thread."""
    decisions, superseded_notice = list_decisions(
        db, project_id=thread.project_id, planner_thread_id=thread.planner_thread_id, active_only=True
    )
    digests = get_thread_digests(db, thread.planner_thread_id)
    recent_turns = get_recent_messages(db, thread.planner_thread_id, limit=chat_turn_limit)
    active_tasks = list(
        db.scalars(
            select(ExecutionTask)
            .where(
                ExecutionTask.planner_thread_id == thread.planner_thread_id,
                ExecutionTask.status != "COMPLETED",
            )
            .order_by(ExecutionTask.updated_at.desc())
        ).all()
    )
    return ProjectContextOut(
        thread=thread,  # type: ignore[arg-type]
        active_decisions=decisions,  # type: ignore[arg-type]
        superseded_notice=superseded_notice,
        digests=[
            {
                "digest_id": d.digest_id,
                "start_turn": d.start_turn,
                "end_turn": d.end_turn,
                "digest_content": d.digest_content,
                "summarizer_model": d.summarizer_model,
            }
            for d in digests
        ],
        recent_chat_turns=recent_turns,  # type: ignore[arg-type]
        active_tasks=active_tasks,  # type: ignore[arg-type]
    )


def start_work_from_chat(
    db: Session, actor: Actor, req: StartWorkFromChatRequest
) -> tuple[ExecutionTask, ProjectContextOut]:
    """Resolve chat_ref by name/title, create+claim an EXECUTING task, and return initial context."""
    thread = resolve_chat_thread(db, req.chat_ref)
    lease_expires = utc_now() + timedelta(minutes=get_settings().default_lease_minutes)
    task = ExecutionTask(
        project_id=thread.project_id,
        planner_thread_id=thread.planner_thread_id,
        title=req.task_title,
        goal_description=req.goal_description,
        acceptance_criteria=req.acceptance_criteria,
        status="EXECUTING",
        assigned_agent=actor.actor_id,
        lease_owner=actor.actor_id,
        lease_session_id=req.session_id,
        lease_expires_at=lease_expires,
        local_repo_path=req.local_repo_path,
        git_branch=req.git_branch,
        worktree_path=req.worktree_path,
    )
    db.add(task)
    db.flush()
    record_audit(
        db,
        actor=actor,
        action="work_started_from_chat",
        target_type="task",
        target_id=task.task_id,
        detail={"chat_ref": req.chat_ref, "planner_thread_id": thread.planner_thread_id},
    )
    db.commit()
    db.refresh(task)
    ctx = build_project_context(db, thread, chat_turn_limit=req.chat_turn_limit)
    return task, ctx


def list_pending_tasks(db: Session, project_id: str | None = None) -> list[ExecutionTask]:
    """List PLANNED, HANDOFF_PENDING, or BLOCKED_QUOTA tasks available to claim."""
    stmt = (
        select(ExecutionTask)
        .where(ExecutionTask.status.in_(["PLANNED", "HANDOFF_PENDING", "BLOCKED_QUOTA"]))
        .order_by(ExecutionTask.updated_at.desc())
    )
    if project_id:
        stmt = stmt.where(ExecutionTask.project_id == project_id)
    return list(db.scalars(stmt).all())


def claim_task(
    db: Session, actor: Actor, task_id: str, req: TaskClaimRequest
) -> ExecutionTask:
    """Atomically acquire an execution lease on a PLANNED or expired-lease task."""
    task = _lock_task(db, task_id)
    now = utc_now()
    expires = _as_utc(task.lease_expires_at)

    if task.status in ("COMPLETED", "FAILED"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot claim task in terminal status '{task.status}'",
        )
    if task.status in ("HANDOFF_PENDING", "BLOCKED_QUOTA"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Task is awaiting handoff; use claim_handoff endpoint instead",
        )
    if task.lease_owner and task.lease_owner != actor.actor_id and expires and expires > now:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Task is currently leased by '{task.lease_owner}' until {expires.isoformat()}",
        )

    task.assigned_agent = actor.actor_id
    task.lease_owner = actor.actor_id
    task.lease_session_id = req.session_id
    task.lease_expires_at = now + timedelta(minutes=req.lease_minutes)
    if task.status == "PLANNED":
        _transition(task, "EXECUTING")

    record_audit(
        db,
        actor=actor,
        action="task_claimed",
        target_type="task",
        target_id=task.task_id,
        detail={"session_id": req.session_id},
    )
    db.commit()
    db.refresh(task)
    return task


def report_progress(
    db: Session, actor: Actor, task_id: str, req: TaskProgressRequest
) -> TaskCheckpoint:
    """Record a structured progress checkpoint and renew the active lease."""
    task = _lock_task(db, task_id)
    _assert_lease_holder(task, actor, req.session_id)
    if task.status == "RESUMED":
        _transition(task, "EXECUTING")
    task.lease_expires_at = utc_now() + timedelta(minutes=get_settings().default_lease_minutes)

    ckpt = TaskCheckpoint(
        task_id=task.task_id,
        agent_id=actor.actor_id,
        session_id=req.session_id,
        checkpoint_type="progress",
        completed_steps=req.completed_steps,
        current_step=req.current_step,
        next_steps=req.next_steps,
        execution_digest=req.execution_digest,
        local_artifact_refs=req.local_artifact_refs,
    )
    db.add(ckpt)
    db.commit()
    db.refresh(ckpt)
    return ckpt


def handoff_task(
    db: Session, actor: Actor, task_id: str, req: TaskHandoffRequest
) -> TaskCheckpoint:
    """Save handoff checkpoint (including latest ~10 execution turns & git diff) and release lease."""
    max_patch = get_settings().max_patch_bytes
    if req.git_diff_patch and len(req.git_diff_patch.encode("utf-8")) > max_patch:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"git_diff_patch exceeds inline limit ({max_patch} bytes); save patch path in local_artifact_refs instead",
        )

    task = _lock_task(db, task_id)
    _assert_lease_holder(task, actor, req.session_id)

    target_status = "BLOCKED_QUOTA" if req.reason == "quota_exhausted" else "HANDOFF_PENDING"
    _transition(task, target_status)
    task.lease_owner = None
    task.lease_session_id = None
    task.lease_expires_at = None

    ckpt = TaskCheckpoint(
        task_id=task.task_id,
        agent_id=actor.actor_id,
        session_id=req.session_id,
        checkpoint_type="quota_exhausted" if req.reason == "quota_exhausted" else "handoff",
        completed_steps=req.completed_steps,
        current_step=req.current_step,
        next_steps=req.next_steps,
        blockers=req.blockers,
        execution_digest=req.execution_digest,
        execution_turns_json=req.recent_turns[-10:],
        execution_trace_excerpt=req.execution_trace_excerpt,
        git_commit=req.git_commit,
        git_diff_patch=req.git_diff_patch,
        local_artifact_refs=req.local_artifact_refs,
    )
    db.add(ckpt)
    record_audit(
        db,
        actor=actor,
        action="task_handoff_initiated",
        target_type="task",
        target_id=task.task_id,
        detail={"reason": req.reason, "turns_saved": len(ckpt.execution_turns_json)},
    )
    db.commit()
    db.refresh(ckpt)
    return ckpt


def claim_handoff(
    db: Session, actor: Actor, task_id: str, req: TaskClaimRequest
) -> ExecutionTask:
    """Atomically claim a pending handoff or quota-blocked task for another agent."""
    task = _lock_task(db, task_id)
    if task.status not in ("HANDOFF_PENDING", "BLOCKED_QUOTA"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Task '{task_id}' is not awaiting handoff (current: {task.status})",
        )
    _transition(task, "HANDED_OFF")
    task.assigned_agent = actor.actor_id
    task.lease_owner = actor.actor_id
    task.lease_session_id = req.session_id
    task.lease_expires_at = utc_now() + timedelta(minutes=req.lease_minutes)

    record_audit(
        db,
        actor=actor,
        action="handoff_claimed",
        target_type="task",
        target_id=task.task_id,
        detail={"new_agent": actor.actor_id, "session_id": req.session_id},
    )
    db.commit()
    db.refresh(task)
    return task


def get_handoff_package(db: Session, task_id: str) -> HandoffPackageOut:
    """Build concise relay context: active decisions + ~5 ChatGPT turns + ~10 execution turns."""
    task = db.get(ExecutionTask, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    latest_ckpt = db.scalar(
        select(TaskCheckpoint)
        .where(TaskCheckpoint.task_id == task_id)
        .order_by(TaskCheckpoint.created_at.desc())
        .limit(1)
    )
    if latest_ckpt is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No checkpoint/handoff record exists for this task",
        )

    decisions, superseded_notice = list_decisions(
        db, project_id=task.project_id, planner_thread_id=task.planner_thread_id, active_only=True
    )
    recent_chat = get_recent_messages(db, task.planner_thread_id, limit=5)

    return HandoffPackageOut(
        task=task,  # type: ignore[arg-type]
        active_decisions=decisions,  # type: ignore[arg-type]
        superseded_notice=superseded_notice,
        recent_chat_turns=recent_chat,  # type: ignore[arg-type]
        previous_execution_digest=latest_ckpt.execution_digest,
        previous_execution_recent_turns=latest_ckpt.execution_turns_json[-10:],
        previous_execution_trace_excerpt=latest_ckpt.execution_trace_excerpt,
        completed_steps=latest_ckpt.completed_steps,
        current_step=latest_ckpt.current_step,
        next_steps=latest_ckpt.next_steps,
        blockers=latest_ckpt.blockers,
        git_commit=latest_ckpt.git_commit,
        git_diff_patch=latest_ckpt.git_diff_patch,
        local_artifact_refs=latest_ckpt.local_artifact_refs,
    )


def confirm_handoff(
    db: Session, actor: Actor, task_id: str, req: ConfirmHandoffRequest
) -> ExecutionTask:
    """Confirm workspace verification and transition HANDED_OFF -> RESUMED."""
    task = _lock_task(db, task_id)
    _assert_lease_holder(task, actor, req.session_id)
    _transition(task, "RESUMED")
    task.lease_expires_at = utc_now() + timedelta(minutes=get_settings().default_lease_minutes)

    record_audit(
        db,
        actor=actor,
        action="handoff_confirmed",
        target_type="task",
        target_id=task.task_id,
        detail={"workspace_verified_note": req.workspace_verified_note},
    )
    db.commit()
    db.refresh(task)
    return task


def update_task_status(
    db: Session, actor: Actor, task_id: str, new_status: str, session_id: str = "", summary: str = ""
) -> ExecutionTask:
    """Validate and apply an explicit status update."""
    task = _lock_task(db, task_id)
    if actor.role == "agent" and task.lease_owner:
        _assert_lease_holder(task, actor, session_id)
    _transition(task, new_status)
    if new_status in ("COMPLETED", "FAILED"):
        task.lease_owner = None
        task.lease_session_id = None
        task.lease_expires_at = None

    record_audit(
        db,
        actor=actor,
        action="task_status_updated",
        target_type="task",
        target_id=task.task_id,
        detail={"new_status": new_status, "summary": summary},
    )
    db.commit()
    db.refresh(task)
    return task
