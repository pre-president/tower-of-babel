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
    CompactionCandidateOut,
    CompactionPlanOut,
    DecisionApproveRequest,
    DecisionOut,
    MessagesSyncRequest,
    MessagesSyncResponse,
    ProjectContextOut,
    QuestionOut,
    QuestionResolveRequest,
    ThreadDigestCreateRequest,
    ThreadDigestOut,
    ThreadOut,
    ThreadRegisterRequest,
)
from app.services.decisions import approve_or_supersede_decision, reject_decision
from app.services.dialogues import (
    build_compaction_plan,
    list_compaction_candidates,
    save_thread_digest,
    search_dialogue,
    sync_messages,
)
from app.services.questions import list_pending_questions, mark_question_in_chat, resolve_question
from app.services.tasks import build_project_context
from app.services.threads import list_threads, resolve_chat_thread, upsert_planner_thread

router = APIRouter(prefix="/api/v1/bridge", tags=["bridge"])


class DecisionRejectRequest(BaseModel):
    reason: str = ""


class ThreadInboxOut(BaseModel):
    planner_thread_id: str
    pending_questions: list[QuestionOut]
    proposed_decisions: list[DecisionOut]


class BridgeContextLoadRequest(BaseModel):
    planner_thread_id: str
    search_query: str | None = None
    chat_turn_limit: int = 10


class BridgeContextLoadOut(BaseModel):
    context: ProjectContextOut
    search_hits: list[dict]
    formatted_prompt: str


class MarkInChatRequest(BaseModel):
    planner_thread_id: str | None = None


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


@router.post("/questions/{question_id}/in-chat", response_model=QuestionOut)
def mark_question_sent_to_chat(
    question_id: str,
    req: MarkInChatRequest,
    actor: Actor = Depends(require_bridge),
    db: Session = Depends(get_db),
) -> QuestionOut:
    return mark_question_in_chat(
        db, actor, question_id, planner_thread_id=req.planner_thread_id
    )  # type: ignore[return-value]


@router.get("/inbox/triage", response_model=list[QuestionOut])
def get_triage_questions(
    _: Actor = Depends(require_bridge),
    db: Session = Depends(get_db),
) -> list[QuestionOut]:
    return [
        q
        for q in list_pending_questions(db)
        if q.status == "NEEDS_TRIAGE" or not q.planner_thread_id
    ]  # type: ignore[return-value]


@router.post("/context/load", response_model=BridgeContextLoadOut)
def load_bridge_context(
    req: BridgeContextLoadRequest,
    _: Actor = Depends(require_bridge),
    db: Session = Depends(get_db),
) -> BridgeContextLoadOut:
    thread = resolve_chat_thread(db, req.planner_thread_id)
    ctx = build_project_context(db, thread, chat_turn_limit=req.chat_turn_limit)
    hits = (
        search_dialogue(
            db,
            query=req.search_query,
            project_id=thread.project_id,
            limit=5,
        )
        if req.search_query
        else []
    )
    lines = [
        f"## Tower of Babel Context Snapshot ({thread.planner_thread_id}: {thread.title})",
        "### Active APPROVED Decisions",
    ]
    if ctx.active_decisions:
        for d in ctx.active_decisions:
            lines.append(f"- [v{d.decision_version}] **{d.title}**: {d.decision_content}")
    else:
        lines.append("- (None yet)")

    if ctx.active_tasks:
        lines.append("\n### Active Execution Tasks")
        for t in ctx.active_tasks[:5]:
            lines.append(
                f"- `{t.task_id}` [{t.status}] ({t.assigned_agent or 'unassigned'}): {t.title}"
            )

    if hits:
        lines.append(f"\n### Dialogue Search Matches for `{req.search_query}`")
        for h in hits:
            lines.append(f"- [{h.planner_thread_id} #{h.turn_index} {h.role}] {h.content[:280]}")

    return BridgeContextLoadOut(
        context=ctx,
        search_hits=[
            {
                "message_id": h.message_id,
                "planner_thread_id": h.planner_thread_id,
                "turn_index": h.turn_index,
                "role": h.role,
                "content": h.content,
            }
            for h in hits
        ],
        formatted_prompt="\n".join(lines),
    )


@router.get("/compaction/candidates", response_model=list[CompactionCandidateOut])
def get_compaction_candidates(
    protect_recent_turns: int = Query(default=20, ge=0, le=100),
    threshold_chars: int = Query(default=150_000, ge=1000, le=1_000_000),
    _: Actor = Depends(require_bridge),
    db: Session = Depends(get_db),
) -> list[CompactionCandidateOut]:
    return list_compaction_candidates(
        db,
        protect_recent_turns=protect_recent_turns,
        threshold_chars=threshold_chars,
    )


@router.get("/threads/{planner_thread_id}/compaction-plan", response_model=CompactionPlanOut)
def get_thread_compaction_plan(
    planner_thread_id: str,
    protect_recent_turns: int = Query(default=20, ge=0, le=100),
    threshold_chars: int = Query(default=150_000, ge=1000, le=1_000_000),
    max_batch_chars: int = Query(default=250_000, ge=5000, le=800_000),
    force: bool = Query(default=False),
    _: Actor = Depends(require_bridge),
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
def create_bridge_digest(
    planner_thread_id: str,
    req: ThreadDigestCreateRequest,
    actor: Actor = Depends(require_bridge),
    db: Session = Depends(get_db),
) -> ThreadDigestOut:
    return save_thread_digest(db, actor, planner_thread_id, req)  # type: ignore[return-value]
