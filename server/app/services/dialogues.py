"""Dialogue Archive ingestion (idempotent), search, and rolling digest queries."""

from __future__ import annotations

import hashlib
from fastapi import HTTPException, status
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import Actor
from app.models import DialogueMessage, PlannerThread, ThreadDigest, utc_now
from app.schemas import MessagesSyncRequest, MessagesSyncResponse
from app.services.audit import record_audit


def compute_message_hash(
    *,
    source_platform: str,
    planner_thread_id: str,
    role: str,
    content: str,
    external_message_id: str | None,
) -> str:
    """Compute a deterministic SHA-256 hash for idempotent message deduplication."""
    normalized = content.strip()
    identity_part = external_message_id or ""
    payload = f"{source_platform}|{planner_thread_id}|{role}|{identity_part}|{normalized}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sync_messages(db: Session, actor: Actor, req: MessagesSyncRequest) -> MessagesSyncResponse:
    """Incrementally persist visible dialogue messages with hash deduplication."""
    thread = db.get(PlannerThread, req.planner_thread_id)
    if thread is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Planner thread '{req.planner_thread_id}' is not registered",
        )
    if not thread.sync_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Sync is disabled for planner thread '{req.planner_thread_id}'",
        )

    current_max_turn = db.scalar(
        select(func.coalesce(func.max(DialogueMessage.turn_index), 0)).where(
            DialogueMessage.planner_thread_id == req.planner_thread_id
        )
    )
    next_turn = int(current_max_turn or 0) + 1

    inserted_count = 0
    duplicate_count = 0
    added_chars = 0

    for item in req.messages:
        msg_hash = compute_message_hash(
            source_platform=req.source_platform,
            planner_thread_id=req.planner_thread_id,
            role=item.role,
            content=item.content,
            external_message_id=item.external_message_id,
        )
        existing = db.scalar(
            select(DialogueMessage.message_id).where(DialogueMessage.message_hash == msg_hash)
        )
        if existing is not None:
            duplicate_count += 1
            continue

        content_clean = item.content.strip()
        char_len = len(content_clean)
        msg = DialogueMessage(
            source_platform=req.source_platform,
            planner_thread_id=req.planner_thread_id,
            task_id=req.task_id,
            agent_id=actor.actor_id if actor.role == "agent" else None,
            external_conversation_id=req.external_conversation_id,
            external_message_id=item.external_message_id,
            turn_index=next_turn,
            role=item.role,
            content=content_clean,
            content_chars=char_len,
            message_hash=msg_hash,
            attachment_refs=item.attachment_refs,
            source_timestamp=item.source_timestamp,
        )
        db.add(msg)
        next_turn += 1
        inserted_count += 1
        added_chars += char_len

    thread.last_synced_at = utc_now()
    thread.uncompressed_char_count += added_chars
    record_audit(
        db,
        actor=actor,
        action="messages_synced",
        target_type="planner_thread",
        target_id=thread.planner_thread_id,
        detail={"inserted": inserted_count, "duplicates": duplicate_count},
    )
    db.commit()

    return MessagesSyncResponse(
        planner_thread_id=thread.planner_thread_id,
        inserted_count=inserted_count,
        duplicate_count=duplicate_count,
        uncompressed_char_count=thread.uncompressed_char_count,
    )


def get_recent_messages(
    db: Session, planner_thread_id: str, limit: int = 20
) -> list[DialogueMessage]:
    """Fetch the latest N visible turns in chronological order."""
    stmt = (
        select(DialogueMessage)
        .where(DialogueMessage.planner_thread_id == planner_thread_id)
        .order_by(DialogueMessage.turn_index.desc())
        .limit(limit)
    )
    rows = list(db.scalars(stmt).all())
    rows.reverse()
    return rows


def get_conversation_range(
    db: Session, planner_thread_id: str, start_turn: int, end_turn: int
) -> list[DialogueMessage]:
    """Fetch visible turns in an explicit turn_index range [start_turn, end_turn]."""
    stmt = (
        select(DialogueMessage)
        .where(
            DialogueMessage.planner_thread_id == planner_thread_id,
            DialogueMessage.turn_index >= start_turn,
            DialogueMessage.turn_index <= end_turn,
        )
        .order_by(DialogueMessage.turn_index.asc())
    )
    return list(db.scalars(stmt).all())


def search_dialogue(
    db: Session,
    *,
    query: str,
    project_id: str | None = None,
    planner_thread_id: str | None = None,
    limit: int = 8,
) -> list[DialogueMessage]:
    """Search dialogue archive using PostgreSQL trigram/tsvector or portable ILIKE fallback."""
    clean_query = query.strip()
    if not clean_query:
        return []

    stmt = select(DialogueMessage)
    if planner_thread_id:
        stmt = stmt.where(DialogueMessage.planner_thread_id == planner_thread_id)
    elif project_id:
        stmt = stmt.join(
            PlannerThread, PlannerThread.planner_thread_id == DialogueMessage.planner_thread_id
        ).where(PlannerThread.project_id == project_id)

    dialect_name = db.bind.dialect.name if db.bind is not None else "sqlite"
    if dialect_name == "postgresql":
        # Match either full-text lexemes or trigram substring (handles Chinese phrases cleanly)
        stmt = stmt.where(
            text(
                "(to_tsvector('simple', dialogue_messages.content) @@ plainto_tsquery('simple', :q) "
                "OR dialogue_messages.content ILIKE :pattern)"
            )
        ).params(q=clean_query, pattern=f"%{clean_query}%")
    else:
        stmt = stmt.where(DialogueMessage.content.ilike(f"%{clean_query}%"))

    stmt = stmt.order_by(DialogueMessage.created_at.desc()).limit(limit)
    return list(db.scalars(stmt).all())


def get_thread_digests(db: Session, planner_thread_id: str) -> list[ThreadDigest]:
    """Return all chronological DeepSeek rolling digests for a thread."""
    stmt = (
        select(ThreadDigest)
        .where(ThreadDigest.planner_thread_id == planner_thread_id)
        .order_by(ThreadDigest.start_turn.asc())
    )
    return list(db.scalars(stmt).all())
