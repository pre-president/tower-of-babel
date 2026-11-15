"""Dialogue Archive ingestion (idempotent), search, and rolling digest queries."""

from __future__ import annotations

import hashlib
from fastapi import HTTPException, status
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import Actor
from app.models import DialogueMessage, PlannerThread, ThreadDigest, utc_now
from app.schemas import (
    CompactionCandidateOut,
    CompactionPlanOut,
    MessagesSyncRequest,
    MessagesSyncResponse,
    ThreadDigestCreateRequest,
)
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


def _highest_covered_turn(db: Session, planner_thread_id: str) -> int:
    val = db.scalar(
        select(func.coalesce(func.max(ThreadDigest.end_turn), 0)).where(
            ThreadDigest.planner_thread_id == planner_thread_id
        )
    )
    return int(val or 0)


def _compute_eligible_messages(
    db: Session,
    planner_thread_id: str,
    *,
    protect_recent_turns: int = 20,
) -> tuple[PlannerThread, int, int, list[DialogueMessage]]:
    thread = db.get(PlannerThread, planner_thread_id)
    if thread is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Planner thread '{planner_thread_id}' not found",
        )
    all_turns = list(
        db.scalars(
            select(DialogueMessage)
            .where(DialogueMessage.planner_thread_id == planner_thread_id)
            .order_by(DialogueMessage.turn_index.asc())
        ).all()
    )
    covered_until = _highest_covered_turn(db, planner_thread_id)
    cutoff_count = max(0, len(all_turns) - protect_recent_turns)
    protected_slice = all_turns[:cutoff_count]
    eligible = [m for m in protected_slice if m.turn_index > covered_until]
    return thread, len(all_turns), covered_until, eligible


def build_compaction_plan(
    db: Session,
    planner_thread_id: str,
    *,
    protect_recent_turns: int = 20,
    threshold_chars: int = 150_000,
    max_batch_chars: int = 250_000,
    force: bool = False,
) -> CompactionPlanOut:
    """Identify uncovered turns outside the latest 20-turn protection window ready for DeepSeek 1M."""
    thread, total_turns, covered_until, eligible = _compute_eligible_messages(
        db, planner_thread_id, protect_recent_turns=protect_recent_turns
    )
    eligible_chars = sum(m.content_chars for m in eligible)
    should_compact = (eligible_chars >= threshold_chars) or (force and eligible_chars > 0)

    batch: list[DialogueMessage] = []
    batch_chars = 0
    if should_compact and eligible:
        for msg in eligible:
            if batch and (batch_chars + msg.content_chars > max_batch_chars):
                break
            batch.append(msg)
            batch_chars += msg.content_chars

    return CompactionPlanOut(
        planner_thread_id=thread.planner_thread_id,
        project_id=thread.project_id,
        title=thread.title,
        total_turns=total_turns,
        covered_until_turn=covered_until,
        protected_recent_turns=protect_recent_turns,
        eligible_char_count=eligible_chars,
        threshold_chars=threshold_chars,
        should_compact=should_compact,
        start_turn=batch[0].turn_index if batch else None,
        end_turn=batch[-1].turn_index if batch else None,
        batch_char_count=batch_chars,
        messages=batch,  # type: ignore[arg-type]
    )


def list_compaction_candidates(
    db: Session,
    *,
    protect_recent_turns: int = 20,
    threshold_chars: int = 150_000,
) -> list[CompactionCandidateOut]:
    """Summarize compaction readiness across all whitelisted planner threads."""
    threads = list(
        db.scalars(
            select(PlannerThread)
            .where(PlannerThread.sync_enabled.is_(True))
            .order_by(PlannerThread.uncompressed_char_count.desc())
        ).all()
    )
    out: list[CompactionCandidateOut] = []
    for t in threads:
        _, total_turns, covered_until, eligible = _compute_eligible_messages(
            db, t.planner_thread_id, protect_recent_turns=protect_recent_turns
        )
        eligible_chars = sum(m.content_chars for m in eligible)
        out.append(
            CompactionCandidateOut(
                planner_thread_id=t.planner_thread_id,
                project_id=t.project_id,
                title=t.title,
                total_turns=total_turns,
                covered_until_turn=covered_until,
                protected_recent_turns=protect_recent_turns,
                eligible_char_count=eligible_chars,
                threshold_chars=threshold_chars,
                should_compact=eligible_chars >= threshold_chars,
            )
        )
    return out


def save_thread_digest(
    db: Session,
    actor: Actor,
    planner_thread_id: str,
    req: ThreadDigestCreateRequest,
) -> ThreadDigest:
    """Persist a structured rolling digest while keeping all original dialogue rows untouched."""
    if req.start_turn > req.end_turn:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_turn must be <= end_turn",
        )
    thread = db.scalar(
        select(PlannerThread).where(PlannerThread.planner_thread_id == planner_thread_id).with_for_update()
    )
    if thread is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Planner thread '{planner_thread_id}' not found",
        )

    # Ensure protected tail turns remain untouched
    total_turns = int(
        db.scalar(
            select(func.count(DialogueMessage.message_id)).where(
                DialogueMessage.planner_thread_id == planner_thread_id
            )
        )
        or 0
    )
    max_compressible_idx = max(0, total_turns - req.protect_recent_turns)
    all_turn_indices = list(
        db.scalars(
            select(DialogueMessage.turn_index)
            .where(DialogueMessage.planner_thread_id == planner_thread_id)
            .order_by(DialogueMessage.turn_index.asc())
        ).all()
    )
    if req.protect_recent_turns > 0 and all_turn_indices:
        protected_start = (
            all_turn_indices[-req.protect_recent_turns]
            if len(all_turn_indices) >= req.protect_recent_turns
            else all_turn_indices[0]
        )
        if max_compressible_idx == 0 or req.end_turn >= protected_start:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot compress inside the latest {req.protect_recent_turns} protected turns",
            )

    existing_digests = get_thread_digests(db, planner_thread_id)
    for d in existing_digests:
        if d.start_turn == req.start_turn and d.end_turn == req.end_turn:
            return d  # Idempotent replay
        if not (req.end_turn < d.start_turn or req.start_turn > d.end_turn):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Turn span [{req.start_turn}..{req.end_turn}] overlaps existing digest [{d.start_turn}..{d.end_turn}]",
            )

    digest_clean = req.digest_content.strip()
    digest = ThreadDigest(
        planner_thread_id=planner_thread_id,
        project_id=thread.project_id,
        start_turn=req.start_turn,
        end_turn=req.end_turn,
        source_char_count=req.source_char_count,
        digest_char_count=len(digest_clean),
        digest_content=digest_clean,
        summarizer_model=req.summarizer_model,
    )
    db.add(digest)
    db.flush()

    # Recompute exact remaining uncompressed character count from uncovered messages
    covered_spans = [(d.start_turn, d.end_turn) for d in existing_digests] + [
        (req.start_turn, req.end_turn)
    ]
    rows = db.execute(
        select(DialogueMessage.turn_index, DialogueMessage.content_chars).where(
            DialogueMessage.planner_thread_id == planner_thread_id
        )
    ).all()
    remaining_chars = sum(
        chars
        for turn_idx, chars in rows
        if not any(s <= turn_idx <= e for s, e in covered_spans)
    )
    thread.uncompressed_char_count = remaining_chars

    record_audit(
        db,
        actor=actor,
        action="digest_created",
        target_type="thread_digest",
        target_id=digest.digest_id,
        detail={
            "planner_thread_id": planner_thread_id,
            "start_turn": req.start_turn,
            "end_turn": req.end_turn,
            "source_char_count": req.source_char_count,
            "digest_char_count": len(digest_clean),
            "remaining_uncompressed_chars": remaining_chars,
        },
    )
    db.commit()
    db.refresh(digest)
    return digest
