"""Audit logging helper."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import Actor
from app.models import AuditLog


def record_audit(
    db: Session,
    *,
    actor: Actor,
    action: str,
    target_type: str,
    target_id: str,
    detail: dict | None = None,
) -> AuditLog:
    """Append an immutable audit log entry inside the active transaction."""
    entry = AuditLog(
        actor_role=actor.role,
        actor_id=actor.actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail_json=detail or {},
    )
    db.add(entry)
    return entry
