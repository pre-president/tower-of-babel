"""Bearer token authentication and strict role-based permission guards."""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import Actor, Settings, get_settings
from app.db import get_db
from app.services.audit import record_audit

_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_actor(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> Actor:
    """Validate Bearer token and resolve caller identity."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Bearer token",
        )
    actor = settings.resolve_token(credentials.credentials)
    if actor is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )
    return actor


def require_bridge(
    actor: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> Actor:
    """Restrict endpoint exclusively to the user-authorized Chat Bridge role."""
    if actor.role != "bridge":
        record_audit(
            db,
            actor=actor,
            action="auth_denied_bridge_only",
            target_type="auth",
            target_id=actor.actor_id,
            detail={"reason": "Agents cannot perform Bridge/approval actions"},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: this operation requires Bridge authorization",
        )
    return actor
