"""Hub execution and context API endpoints (for Agents & Bridge)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_actor, require_bridge_or_summarizer
from app.config import Actor
from app.db import get_db
from app.schemas import (
    ArtifactOut,
    ArtifactRegisterRequest,
    CheckpointTraceOut,
    CompactionPlanOut,
    ConfirmHandoffRequest,
    DecisionOut,
    DecisionProposeRequest,
    DialogueMessageOut,
    HandoffPackageOut,
    OrmModel,
    ProjectContextOut,
    QuestionOut,
    QuestionSubmitRequest,
    StartWorkFromChatRequest,
    TaskClaimRequest,
    TaskContextOut,
    TaskCreateRequest,
    TaskHandoffRequest,
    TaskOut,
    TaskProgressRequest,
    TaskStatus,
    ThreadDigestCreateRequest,
    ThreadDigestOut,
    ThreadOut,
)
from app.services.artifacts import list_artifacts, register_artifact
from app.services.decisions import get_decision_by_id, list_decisions, propose_decision
from app.services.dialogues import (
    build_compaction_plan,
    get_conversation_range,
    get_recent_messages,
    get_thread_digests,
    save_thread_digest,
    search_dialogue,
)
from app.services.questions import get_question_by_id, list_pending_questions, submit_question
from app.services.tasks import (
    build_project_context,
    claim_handoff,
    claim_task,
    confirm_handoff,
    create_task,
    get_checkpoint_trace,
    get_execution_status,
    get_handoff_package,
    get_task_context,
    handoff_task,
    list_pending_tasks,
    report_progress,
    start_work_from_chat,
    update_task_status,
)
from app.services.threads import list_threads, resolve_chat_thread

router = APIRouter(prefix="/api/v1/hub", tags=["hub"])


class StartWorkOut(BaseModel):
    task: TaskOut
    context: ProjectContextOut


class DecisionListOut(BaseModel):
    decisions: list[DecisionOut]
    superseded_notice: list[dict]


class CheckpointOut(OrmModel):
    checkpoint_id: str
    task_id: str
    agent_id: str
    checkpoint_type: str
    current_step: str


class TaskStatusUpdateRequest(BaseModel):
    status: TaskStatus
    session_id: str = ""
    summary: str = ""


@router.get("/threads", response_model=list[ThreadOut])
def list_threads_endpoint(
    project_id: str | None = Query(default=None),
    _: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> list[ThreadOut]:
    return list_threads(db, project_id=project_id)  # type: ignore[return-value]


@router.get("/threads/resolve", response_model=ThreadOut)
def resolve_thread_endpoint(
    chat_ref: str = Query(..., min_length=1),
    _: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> ThreadOut:
    return resolve_chat_thread(db, chat_ref)  # type: ignore[return-value]


@router.get("/context", response_model=ProjectContextOut)
def get_context_endpoint(
    chat_ref: str = Query(..., min_length=1),
    chat_turn_limit: int = Query(default=20, ge=1, le=50),
    _: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> ProjectContextOut:
    thread = resolve_chat_thread(db, chat_ref)
    return build_project_context(db, thread, chat_turn_limit=chat_turn_limit)


@router.post("/work/start", response_model=StartWorkOut)
def start_work_endpoint(
    req: StartWorkFromChatRequest,
    actor: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> StartWorkOut:
    task, ctx = start_work_from_chat(db, actor, req)
    return StartWorkOut(task=task, context=ctx)  # type: ignore[arg-type]


@router.get("/dialogues/search", response_model=list[DialogueMessageOut])
def search_dialogues_endpoint(
    q: str = Query(..., min_length=1),
    project_id: str | None = Query(default=None),
    planner_thread_id: str | None = Query(default=None),
    limit: int = Query(default=8, ge=1, le=30),
    _: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> list[DialogueMessageOut]:
    return search_dialogue(  # type: ignore[return-value]
        db, query=q, project_id=project_id, planner_thread_id=planner_thread_id, limit=limit
    )


@router.get("/dialogues/{planner_thread_id}/recent", response_model=list[DialogueMessageOut])
def recent_dialogues_endpoint(
    planner_thread_id: str,
    limit: int = Query(default=10, ge=1, le=50),
    _: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> list[DialogueMessageOut]:
    return get_recent_messages(db, planner_thread_id, limit=limit)  # type: ignore[return-value]


@router.get("/dialogues/{planner_thread_id}/range", response_model=list[DialogueMessageOut])
def range_dialogues_endpoint(
    planner_thread_id: str,
    start_turn: int = Query(..., ge=1),
    end_turn: int = Query(..., ge=1),
    _: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> list[DialogueMessageOut]:
    return get_conversation_range(db, planner_thread_id, start_turn, end_turn)  # type: ignore[return-value]


@router.get("/threads/{planner_thread_id}/digests", response_model=list[ThreadDigestOut])
def list_digests_endpoint(
    planner_thread_id: str,
    _: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> list[ThreadDigestOut]:
    return get_thread_digests(db, planner_thread_id)  # type: ignore[return-value]


@router.get("/threads/{planner_thread_id}/compaction-plan", response_model=CompactionPlanOut)
def hub_compaction_plan_endpoint(
    planner_thread_id: str,
    protect_recent_turns: int = Query(default=20, ge=0, le=100),
    threshold_chars: int = Query(default=150_000, ge=1000, le=1_000_000),
    max_batch_chars: int = Query(default=250_000, ge=5000, le=800_000),
    force: bool = Query(default=False),
    _: Actor = Depends(require_bridge_or_summarizer),
    db: Session = Depends(get_db),
) -> CompactionPlanOut:
    return build_compaction_plan(
        db,
        planner_thread_id,
        protect_recent_turns=protect_recent_turns,
        threshold_chars=threshold_chars,
        max_batch_chars=max_batch_chars,
        force=force,
    )


@router.post("/threads/{planner_thread_id}/digests", response_model=ThreadDigestOut)
def hub_create_digest_endpoint(
    planner_thread_id: str,
    req: ThreadDigestCreateRequest,
    actor: Actor = Depends(require_bridge_or_summarizer),
    db: Session = Depends(get_db),
) -> ThreadDigestOut:
    return save_thread_digest(db, actor, planner_thread_id, req)  # type: ignore[return-value]


@router.get("/decisions", response_model=DecisionListOut)
def list_decisions_endpoint(
    project_id: str | None = Query(default=None),
    planner_thread_id: str | None = Query(default=None),
    active_only: bool = Query(default=True),
    _: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> DecisionListOut:
    rows, notices = list_decisions(
        db, project_id=project_id, planner_thread_id=planner_thread_id, active_only=active_only
    )
    return DecisionListOut(decisions=rows, superseded_notice=notices)  # type: ignore[arg-type]


@router.get("/decisions/{decision_id}", response_model=DecisionOut)
def get_decision_endpoint(
    decision_id: str,
    _: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> DecisionOut:
    return get_decision_by_id(db, decision_id)  # type: ignore[return-value]


@router.post("/decisions/propose", response_model=DecisionOut)
def propose_decision_endpoint(
    req: DecisionProposeRequest,
    actor: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> DecisionOut:
    return propose_decision(db, actor, req)  # type: ignore[return-value]


@router.post("/tasks", response_model=TaskOut)
def create_task_endpoint(
    req: TaskCreateRequest,
    actor: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> TaskOut:
    return create_task(db, actor, req)  # type: ignore[return-value]


@router.get("/tasks/pending", response_model=list[TaskOut])
def pending_tasks_endpoint(
    project_id: str | None = Query(default=None),
    _: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> list[TaskOut]:
    return list_pending_tasks(db, project_id=project_id)  # type: ignore[return-value]


@router.get("/execution-status", response_model=list[TaskOut])
def execution_status_endpoint(
    project_id: str | None = Query(default=None),
    planner_thread_id: str | None = Query(default=None),
    _: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> list[TaskOut]:
    return get_execution_status(  # type: ignore[return-value]
        db, project_id=project_id, planner_thread_id=planner_thread_id
    )


@router.get("/tasks/{task_id}/context", response_model=TaskContextOut)
def task_context_endpoint(
    task_id: str,
    chat_turn_limit: int = Query(default=20, ge=1, le=50),
    _: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> TaskContextOut:
    return get_task_context(db, task_id, chat_turn_limit=chat_turn_limit)


@router.get("/tasks/{task_id}/trace", response_model=CheckpointTraceOut)
def checkpoint_trace_endpoint(
    task_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=50),
    _: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> CheckpointTraceOut:
    return get_checkpoint_trace(db, task_id, offset=offset, limit=limit)


@router.post("/tasks/{task_id}/claim", response_model=TaskOut)
def claim_task_endpoint(
    task_id: str,
    req: TaskClaimRequest,
    actor: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> TaskOut:
    return claim_task(db, actor, task_id, req)  # type: ignore[return-value]


@router.post("/tasks/{task_id}/progress", response_model=CheckpointOut)
def report_progress_endpoint(
    task_id: str,
    req: TaskProgressRequest,
    actor: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> CheckpointOut:
    return report_progress(db, actor, task_id, req)  # type: ignore[return-value]


@router.post("/tasks/{task_id}/handoff", response_model=CheckpointOut)
def handoff_task_endpoint(
    task_id: str,
    req: TaskHandoffRequest,
    actor: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> CheckpointOut:
    return handoff_task(db, actor, task_id, req)  # type: ignore[return-value]


@router.post("/tasks/{task_id}/handoff/claim", response_model=TaskOut)
def claim_handoff_endpoint(
    task_id: str,
    req: TaskClaimRequest,
    actor: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> TaskOut:
    return claim_handoff(db, actor, task_id, req)  # type: ignore[return-value]


@router.get("/tasks/{task_id}/handoff/package", response_model=HandoffPackageOut)
def handoff_package_endpoint(
    task_id: str,
    _: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> HandoffPackageOut:
    return get_handoff_package(db, task_id)


@router.post("/tasks/{task_id}/handoff/confirm", response_model=TaskOut)
def confirm_handoff_endpoint(
    task_id: str,
    req: ConfirmHandoffRequest,
    actor: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> TaskOut:
    return confirm_handoff(db, actor, task_id, req)  # type: ignore[return-value]


@router.post("/tasks/{task_id}/status", response_model=TaskOut)
def update_status_endpoint(
    task_id: str,
    req: TaskStatusUpdateRequest,
    actor: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> TaskOut:
    return update_task_status(  # type: ignore[return-value]
        db, actor, task_id, req.status, session_id=req.session_id, summary=req.summary
    )


@router.post("/questions", response_model=QuestionOut)
def submit_question_endpoint(
    req: QuestionSubmitRequest,
    actor: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> QuestionOut:
    return submit_question(db, actor, req)  # type: ignore[return-value]


@router.get("/questions/pending", response_model=list[QuestionOut])
def list_questions_endpoint(
    planner_thread_id: str | None = Query(default=None),
    _: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> list[QuestionOut]:
    return list_pending_questions(db, planner_thread_id=planner_thread_id)  # type: ignore[return-value]


@router.get("/questions/{question_id}", response_model=QuestionOut)
def get_question_endpoint(
    question_id: str,
    _: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> QuestionOut:
    return get_question_by_id(db, question_id)  # type: ignore[return-value]


@router.post("/artifacts", response_model=ArtifactOut)
def register_artifact_endpoint(
    req: ArtifactRegisterRequest,
    actor: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> ArtifactOut:
    return register_artifact(db, actor, req)  # type: ignore[return-value]


@router.get("/artifacts", response_model=list[ArtifactOut])
def list_artifacts_endpoint(
    task_id: str | None = Query(default=None),
    project_id: str | None = Query(default=None),
    _: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> list[ArtifactOut]:
    return list_artifacts(db, task_id=task_id, project_id=project_id)  # type: ignore[return-value]
