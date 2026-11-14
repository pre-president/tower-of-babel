"""Relational database models for Tower of Babel Central Hub."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    """Return current UTC timestamp."""
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    """Generate a readable prefixed unique identifier."""
    return f"{prefix}_{uuid4().hex[:16]}"


class Base(DeclarativeBase):
    """Base declarative class for all ORM models."""


class PlannerThread(Base):
    """Registered ChatGPT planning window (whitelisted for sync & routing)."""

    __tablename__ = "planner_threads"

    planner_thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    role_description: Mapped[str] = mapped_column(Text, default="")
    chatgpt_conversation_id: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    chatgpt_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    sync_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_send_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    uncompressed_char_count: Mapped[int] = mapped_column(Integer, default=0)
    pending_question_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class DialogueMessage(Base):
    """Visible dialogue archive (Markdown text only; idempotent via message_hash)."""

    __tablename__ = "dialogue_messages"

    message_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("msg"))
    source_platform: Mapped[str] = mapped_column(String(32), default="chatgpt", index=True)
    planner_thread_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("planner_threads.planner_thread_id"), index=True
    )
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    agent_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    external_conversation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    external_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    turn_index: Mapped[int] = mapped_column(Integer, index=True)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    content_chars: Mapped[int] = mapped_column(Integer)
    message_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    attachment_refs: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        Index("ix_dialogue_thread_turn", "planner_thread_id", "turn_index"),
    )


class ThreadDigest(Base):
    """Rolling compressed summary produced by DeepSeek 1M without deleting originals."""

    __tablename__ = "thread_digests"

    digest_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("dig"))
    planner_thread_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("planner_threads.planner_thread_id"), index=True
    )
    project_id: Mapped[str] = mapped_column(String(64), index=True)
    start_turn: Mapped[int] = mapped_column(Integer)
    end_turn: Mapped[int] = mapped_column(Integer)
    source_char_count: Mapped[int] = mapped_column(Integer)
    digest_char_count: Mapped[int] = mapped_column(Integer)
    digest_content: Mapped[str] = mapped_column(Text)
    summarizer_model: Mapped[str] = mapped_column(String(64), default="deepseek")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Decision(Base):
    """Append-only versioned Decision Archive supporting APPROVED -> SUPERSEDED updates."""

    __tablename__ = "decisions"

    decision_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("dec"))
    project_id: Mapped[str] = mapped_column(String(64), index=True)
    planner_thread_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("planner_threads.planner_thread_id"), index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    decision_content: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text, default="")
    rejected_alternatives: Mapped[str] = mapped_column(Text, default="")
    source_message_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="PROPOSED", index=True)
    supersedes_decision_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("decisions.decision_id"), nullable=True
    )
    decision_version: Mapped[int] = mapped_column(Integer, default=1)
    proposed_by: Mapped[str] = mapped_column(String(64))
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ExecutionTask(Base):
    """Execution Archive task state, lease ownership, and local workspace pointers."""

    __tablename__ = "execution_tasks"

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("task"))
    project_id: Mapped[str] = mapped_column(String(64), index=True)
    planner_thread_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("planner_threads.planner_thread_id"), index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    goal_description: Mapped[str] = mapped_column(Text)
    acceptance_criteria: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="PLANNED", index=True)
    assigned_agent: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(32), nullable=True)
    lease_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    git_branch: Mapped[str | None] = mapped_column(String(128), nullable=True)
    worktree_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    local_repo_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class TaskCheckpoint(Base):
    """Progress checkpoints and model handoff packages (including ~10 recent execution turns)."""

    __tablename__ = "task_checkpoints"

    checkpoint_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("ckpt")
    )
    task_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("execution_tasks.task_id"), index=True
    )
    agent_id: Mapped[str] = mapped_column(String(32))
    session_id: Mapped[str] = mapped_column(String(128))
    checkpoint_type: Mapped[str] = mapped_column(String(32), default="progress")
    completed_steps: Mapped[list[str]] = mapped_column(JSON, default=list)
    current_step: Mapped[str] = mapped_column(Text, default="")
    next_steps: Mapped[list[str]] = mapped_column(JSON, default=list)
    blockers: Mapped[str] = mapped_column(Text, default="")
    execution_digest: Mapped[str] = mapped_column(Text, default="")
    execution_turns_json: Mapped[list[dict]] = mapped_column(JSON, default=list)
    execution_trace_excerpt: Mapped[str] = mapped_column(Text, default="")
    git_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    git_diff_patch: Mapped[str | None] = mapped_column(Text, nullable=True)
    local_artifact_refs: Mapped[list[dict]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Question(Base):
    """Clarification questions routed from execution agents to a specific planner thread."""

    __tablename__ = "questions"

    question_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("q"))
    task_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("execution_tasks.task_id"), nullable=True, index=True
    )
    planner_thread_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("planner_threads.planner_thread_id"), nullable=True, index=True
    )
    asked_by_agent: Mapped[str] = mapped_column(String(32))
    question_text: Mapped[str] = mapped_column(Text)
    evidence_context: Mapped[str] = mapped_column(Text, default="")
    impact_note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    answer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Artifact(Base):
    """Registered local file pointers (Phase 1-2) and optional OSS artifacts (Phase 3)."""

    __tablename__ = "artifacts"

    artifact_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("art"))
    task_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("execution_tasks.task_id"), nullable=True, index=True
    )
    project_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="local_pointer")
    path_or_uri: Mapped[str] = mapped_column(String(1024))
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AuditLog(Base):
    """Immutable audit log of state changes, approvals, handoffs, and auth denials."""

    __tablename__ = "audit_logs"

    log_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("log"))
    actor_role: Mapped[str] = mapped_column(String(24))
    actor_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[str] = mapped_column(String(64))
    detail_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
