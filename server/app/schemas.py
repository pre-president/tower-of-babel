"""Pydantic schemas for REST, Chat Bridge, and Hub context contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class OrmModel(BaseModel):
    """Shared Pydantic base enabling ORM attribute conversion."""

    model_config = ConfigDict(from_attributes=True)


# Built-in defaults: chatgpt, gemini_antigravity, deepseek_claudecode, gpt_codex;
# extensible slug allows cursor, windsurf, claude, ollama, etc.
PlatformType = Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[a-z0-9_-]+$")]
RoleType = Literal["user", "assistant"]
DecisionStatus = Literal["PROPOSED", "APPROVED", "SUPERSEDED", "REJECTED"]
TaskStatus = Literal[
    "PLANNED",
    "EXECUTING",
    "WAITING_FOR_DECISION",
    "BLOCKED_QUOTA",
    "HANDOFF_PENDING",
    "HANDED_OFF",
    "RESUMED",
    "COMPLETED",
    "FAILED",
]


class ThreadRegisterRequest(BaseModel):
    planner_thread_id: str = Field(..., min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    project_id: str = Field(..., min_length=1, max_length=64)
    title: str = Field(..., min_length=1, max_length=255)
    role_description: str = ""
    chatgpt_conversation_id: str | None = Field(default=None, max_length=128)
    chatgpt_url: str | None = Field(default=None, max_length=512)
    sync_enabled: bool = True
    auto_send_enabled: bool = False


class ThreadOut(OrmModel):
    planner_thread_id: str
    project_id: str
    title: str
    role_description: str
    chatgpt_conversation_id: str | None
    chatgpt_url: str | None
    sync_enabled: bool
    auto_send_enabled: bool
    last_synced_at: datetime | None
    uncompressed_char_count: int
    pending_question_count: int


class MessageSyncItem(BaseModel):
    role: RoleType
    content: str = Field(..., min_length=1, max_length=200_000)
    external_message_id: str | None = Field(default=None, max_length=128)
    source_timestamp: datetime | None = None
    attachment_refs: list[dict] | None = None


class MessagesSyncRequest(BaseModel):
    planner_thread_id: str = Field(..., min_length=2, max_length=64)
    source_platform: PlatformType = "chatgpt"
    external_conversation_id: str | None = Field(default=None, max_length=128)
    task_id: str | None = None
    messages: list[MessageSyncItem] = Field(..., min_length=1, max_length=200)


class MessagesSyncResponse(BaseModel):
    planner_thread_id: str
    inserted_count: int
    duplicate_count: int
    uncompressed_char_count: int


class DialogueMessageOut(OrmModel):
    message_id: str
    source_platform: str
    planner_thread_id: str
    turn_index: int
    role: str
    content: str
    content_chars: int
    source_timestamp: datetime | None
    created_at: datetime
    untrusted_historical_dialogue: bool = True


class DecisionProposeRequest(BaseModel):
    project_id: str = Field(..., min_length=1, max_length=64)
    planner_thread_id: str = Field(..., min_length=2, max_length=64)
    title: str = Field(..., min_length=1, max_length=255)
    decision_content: str = Field(..., min_length=1, max_length=20_000)
    rationale: str = ""
    rejected_alternatives: str = ""
    source_message_ids: list[str] = Field(default_factory=list)
    supersedes_decision_id: str | None = None


class DecisionApproveRequest(BaseModel):
    decision_id: str | None = None
    project_id: str | None = None
    planner_thread_id: str | None = None
    title: str | None = None
    decision_content: str | None = None
    rationale: str | None = None
    rejected_alternatives: str | None = None
    source_message_ids: list[str] | None = None
    supersedes_decision_id: str | None = None


class DecisionOut(OrmModel):
    decision_id: str
    project_id: str
    planner_thread_id: str
    title: str
    decision_content: str
    rationale: str
    rejected_alternatives: str
    source_message_ids: list[str]
    status: str
    supersedes_decision_id: str | None
    decision_version: int
    proposed_by: str
    approved_by: str | None
    created_at: datetime
    updated_at: datetime


class TaskCreateRequest(BaseModel):
    project_id: str = Field(..., min_length=1, max_length=64)
    planner_thread_id: str = Field(..., min_length=2, max_length=64)
    title: str = Field(..., min_length=1, max_length=255)
    goal_description: str = Field(..., min_length=1)
    acceptance_criteria: str = ""
    local_repo_path: str | None = Field(default=None, max_length=512)
    git_branch: str | None = Field(default=None, max_length=128)
    worktree_path: str | None = Field(default=None, max_length=512)


class StartWorkFromChatRequest(BaseModel):
    chat_ref: str = Field(..., min_length=1, max_length=255)
    task_title: str = Field(..., min_length=1, max_length=255)
    goal_description: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1, max_length=128)
    acceptance_criteria: str = ""
    local_repo_path: str | None = Field(default=None, max_length=512)
    git_branch: str | None = Field(default=None, max_length=128)
    worktree_path: str | None = Field(default=None, max_length=512)
    chat_turn_limit: int = Field(default=20, ge=1, le=50)


class TaskClaimRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    lease_minutes: int = Field(default=30, ge=5, le=240)


class TaskProgressRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    completed_steps: list[str] = Field(default_factory=list)
    current_step: str = ""
    next_steps: list[str] = Field(default_factory=list)
    execution_digest: str = ""
    local_artifact_refs: list[dict] = Field(default_factory=list)


class TaskHandoffRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    reason: Literal["manual_handoff", "quota_exhausted"] = "manual_handoff"
    execution_digest: str = Field(..., min_length=1)
    completed_steps: list[str] = Field(default_factory=list)
    current_step: str = ""
    next_steps: list[str] = Field(default_factory=list)
    blockers: str = ""
    recent_turns: list[dict] = Field(default_factory=list, max_length=30)
    execution_trace_excerpt: str = ""
    git_commit: str | None = Field(default=None, max_length=64)
    git_diff_patch: str | None = None
    local_artifact_refs: list[dict] = Field(default_factory=list)


class ConfirmHandoffRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    workspace_verified_note: str = ""


class TaskOut(OrmModel):
    task_id: str
    project_id: str
    planner_thread_id: str
    title: str
    goal_description: str
    acceptance_criteria: str
    status: str
    assigned_agent: str | None
    lease_owner: str | None
    lease_session_id: str | None
    lease_expires_at: datetime | None
    git_branch: str | None
    worktree_path: str | None
    local_repo_path: str | None
    created_at: datetime
    updated_at: datetime


class QuestionSubmitRequest(BaseModel):
    task_id: str | None = None
    planner_thread_id: str | None = None
    question_text: str = Field(..., min_length=1, max_length=20_000)
    evidence_context: str = ""
    impact_note: str = ""
    block_task: bool = True


class QuestionResolveRequest(BaseModel):
    answer_text: str = Field(..., min_length=1, max_length=50_000)
    resolution_message_id: str | None = None
    resume_task: bool = True


class QuestionOut(OrmModel):
    question_id: str
    task_id: str | None
    planner_thread_id: str | None
    asked_by_agent: str
    question_text: str
    evidence_context: str
    impact_note: str
    status: str
    answer_text: str | None
    resolution_message_id: str | None
    created_at: datetime
    resolved_at: datetime | None


class ArtifactRegisterRequest(BaseModel):
    project_id: str = Field(..., min_length=1, max_length=64)
    task_id: str | None = None
    kind: Literal["local_pointer", "git_patch", "oss_object"] = "local_pointer"
    path_or_uri: str = Field(..., min_length=1, max_length=1024)
    sha256: str | None = Field(default=None, max_length=64)
    pinned: bool = False
    metadata_json: dict = Field(default_factory=dict)


class ArtifactOut(OrmModel):
    artifact_id: str
    task_id: str | None
    project_id: str
    kind: str
    path_or_uri: str
    sha256: str | None
    pinned: bool
    metadata_json: dict
    created_at: datetime


class ProjectContextOut(BaseModel):
    thread: ThreadOut
    active_decisions: list[DecisionOut]
    superseded_notice: list[dict]
    digests: list[dict]
    recent_chat_turns: list[DialogueMessageOut]
    active_tasks: list[TaskOut]


class HandoffPackageOut(BaseModel):
    task: TaskOut
    active_decisions: list[DecisionOut]
    superseded_notice: list[dict]
    recent_chat_turns: list[DialogueMessageOut]  # Latest ~5 ChatGPT turns
    previous_execution_digest: str
    previous_execution_recent_turns: list[dict]  # Latest ~10 execution turns
    previous_execution_trace_excerpt: str
    completed_steps: list[str]
    current_step: str
    next_steps: list[str]
    blockers: str
    git_commit: str | None
    git_diff_patch: str | None
    local_artifact_refs: list[dict]


class CheckpointDetailOut(OrmModel):
    checkpoint_id: str
    task_id: str
    agent_id: str
    session_id: str
    checkpoint_type: str
    completed_steps: list[str]
    current_step: str
    next_steps: list[str]
    blockers: str
    execution_digest: str
    execution_turns_json: list[dict]
    execution_trace_excerpt: str
    git_commit: str | None
    git_diff_patch: str | None
    local_artifact_refs: list[dict]
    created_at: datetime


class TaskContextOut(BaseModel):
    task: TaskOut
    thread: ThreadOut
    active_decisions: list[DecisionOut]
    superseded_notice: list[dict]
    digests: list[dict]
    recent_chat_turns: list[DialogueMessageOut]
    latest_checkpoint: CheckpointDetailOut | None = None


class CheckpointTraceOut(BaseModel):
    task_id: str
    total_checkpoints: int
    offset: int
    limit: int
    checkpoints: list[CheckpointDetailOut]


class ThreadDigestOut(OrmModel):
    digest_id: str
    planner_thread_id: str
    project_id: str
    start_turn: int
    end_turn: int
    source_char_count: int
    digest_char_count: int
    digest_content: str
    summarizer_model: str
    created_at: datetime


class ThreadDigestCreateRequest(BaseModel):
    start_turn: int = Field(..., ge=1)
    end_turn: int = Field(..., ge=1)
    source_char_count: int = Field(..., ge=1)
    digest_content: str = Field(..., min_length=20, max_length=40_000)
    summarizer_model: str = Field(default="deepseek", min_length=1, max_length=64)
    protect_recent_turns: int = Field(default=20, ge=0, le=100)


class CompactionCandidateOut(BaseModel):
    planner_thread_id: str
    project_id: str
    title: str
    total_turns: int
    covered_until_turn: int
    protected_recent_turns: int
    eligible_char_count: int
    threshold_chars: int
    should_compact: bool


class CompactionPlanOut(BaseModel):
    planner_thread_id: str
    project_id: str
    title: str
    total_turns: int
    covered_until_turn: int
    protected_recent_turns: int
    eligible_char_count: int
    threshold_chars: int
    should_compact: bool
    start_turn: int | None = None
    end_turn: int | None = None
    batch_char_count: int = 0
    messages: list[DialogueMessageOut] = Field(default_factory=list)
