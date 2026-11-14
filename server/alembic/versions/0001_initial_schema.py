"""Initial schema for Tower of Babel Central Hub.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "planner_threads",
        sa.Column("planner_thread_id", sa.String(length=64), primary_key=True),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("role_description", sa.Text(), nullable=False, server_default=""),
        sa.Column("chatgpt_conversation_id", sa.String(length=128), nullable=True, unique=True),
        sa.Column("chatgpt_url", sa.String(length=512), nullable=True),
        sa.Column("sync_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("auto_send_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("uncompressed_char_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pending_question_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_planner_threads_project_id", "planner_threads", ["project_id"])
    op.create_index("ix_planner_threads_title", "planner_threads", ["title"])

    op.create_table(
        "dialogue_messages",
        sa.Column("message_id", sa.String(length=64), primary_key=True),
        sa.Column("source_platform", sa.String(length=32), nullable=False),
        sa.Column(
            "planner_thread_id",
            sa.String(length=64),
            sa.ForeignKey("planner_threads.planner_thread_id"),
            nullable=False,
        ),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("agent_id", sa.String(length=32), nullable=True),
        sa.Column("external_conversation_id", sa.String(length=128), nullable=True),
        sa.Column("external_message_id", sa.String(length=128), nullable=True),
        sa.Column("turn_index", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_chars", sa.Integer(), nullable=False),
        sa.Column("message_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("attachment_refs", sa.JSON(), nullable=True),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_dialogue_thread_turn", "dialogue_messages", ["planner_thread_id", "turn_index"])
    op.create_index("ix_dialogue_messages_task_id", "dialogue_messages", ["task_id"])

    op.create_table(
        "thread_digests",
        sa.Column("digest_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "planner_thread_id",
            sa.String(length=64),
            sa.ForeignKey("planner_threads.planner_thread_id"),
            nullable=False,
        ),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("start_turn", sa.Integer(), nullable=False),
        sa.Column("end_turn", sa.Integer(), nullable=False),
        sa.Column("source_char_count", sa.Integer(), nullable=False),
        sa.Column("digest_char_count", sa.Integer(), nullable=False),
        sa.Column("digest_content", sa.Text(), nullable=False),
        sa.Column("summarizer_model", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_thread_digests_thread", "thread_digests", ["planner_thread_id"])

    op.create_table(
        "decisions",
        sa.Column("decision_id", sa.String(length=64), primary_key=True),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column(
            "planner_thread_id",
            sa.String(length=64),
            sa.ForeignKey("planner_threads.planner_thread_id"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("decision_content", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column("rejected_alternatives", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_message_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column(
            "supersedes_decision_id",
            sa.String(length=64),
            sa.ForeignKey("decisions.decision_id"),
            nullable=True,
        ),
        sa.Column("decision_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("proposed_by", sa.String(length=64), nullable=False),
        sa.Column("approved_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_decisions_project_status", "decisions", ["project_id", "status"])

    op.create_table(
        "execution_tasks",
        sa.Column("task_id", sa.String(length=64), primary_key=True),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column(
            "planner_thread_id",
            sa.String(length=64),
            sa.ForeignKey("planner_threads.planner_thread_id"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("goal_description", sa.Text(), nullable=False),
        sa.Column("acceptance_criteria", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("assigned_agent", sa.String(length=32), nullable=True),
        sa.Column("lease_owner", sa.String(length=32), nullable=True),
        sa.Column("lease_session_id", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("git_branch", sa.String(length=128), nullable=True),
        sa.Column("worktree_path", sa.String(length=512), nullable=True),
        sa.Column("local_repo_path", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_execution_tasks_status", "execution_tasks", ["status"])

    op.create_table(
        "task_checkpoints",
        sa.Column("checkpoint_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(length=64),
            sa.ForeignKey("execution_tasks.task_id"),
            nullable=False,
        ),
        sa.Column("agent_id", sa.String(length=32), nullable=False),
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("checkpoint_type", sa.String(length=32), nullable=False),
        sa.Column("completed_steps", sa.JSON(), nullable=False),
        sa.Column("current_step", sa.Text(), nullable=False, server_default=""),
        sa.Column("next_steps", sa.JSON(), nullable=False),
        sa.Column("blockers", sa.Text(), nullable=False, server_default=""),
        sa.Column("execution_digest", sa.Text(), nullable=False, server_default=""),
        sa.Column("execution_turns_json", sa.JSON(), nullable=False),
        sa.Column("execution_trace_excerpt", sa.Text(), nullable=False, server_default=""),
        sa.Column("git_commit", sa.String(length=64), nullable=True),
        sa.Column("git_diff_patch", sa.Text(), nullable=True),
        sa.Column("local_artifact_refs", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_task_checkpoints_task_id", "task_checkpoints", ["task_id"])

    op.create_table(
        "questions",
        sa.Column("question_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(length=64),
            sa.ForeignKey("execution_tasks.task_id"),
            nullable=True,
        ),
        sa.Column(
            "planner_thread_id",
            sa.String(length=64),
            sa.ForeignKey("planner_threads.planner_thread_id"),
            nullable=True,
        ),
        sa.Column("asked_by_agent", sa.String(length=32), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("evidence_context", sa.Text(), nullable=False, server_default=""),
        sa.Column("impact_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=True),
        sa.Column("resolution_message_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_questions_thread_status", "questions", ["planner_thread_id", "status"])

    op.create_table(
        "artifacts",
        sa.Column("artifact_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(length=64),
            sa.ForeignKey("execution_tasks.task_id"),
            nullable=True,
        ),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("path_or_uri", sa.String(length=1024), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_artifacts_task_id", "artifacts", ["task_id"])

    op.create_table(
        "audit_logs",
        sa.Column("log_id", sa.String(length=64), primary_key=True),
        sa.Column("actor_role", sa.String(length=24), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("detail_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])

    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
        op.execute(
            "CREATE INDEX ix_dialogue_content_trgm ON dialogue_messages "
            "USING gin (content gin_trgm_ops);"
        )
        op.execute(
            "CREATE INDEX ix_dialogue_content_tsv ON dialogue_messages "
            "USING gin (to_tsvector('simple', content));"
        )


def downgrade() -> None:
    for tbl in (
        "audit_logs",
        "artifacts",
        "questions",
        "task_checkpoints",
        "execution_tasks",
        "decisions",
        "thread_digests",
        "dialogue_messages",
        "planner_threads",
    ):
        op.drop_table(tbl)
