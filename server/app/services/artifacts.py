"""Artifact registration (local file pointers in Phase 1-2, OSS in Phase 3)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Actor
from app.models import Artifact
from app.schemas import ArtifactRegisterRequest
from app.services.audit import record_audit


def register_artifact(db: Session, actor: Actor, req: ArtifactRegisterRequest) -> Artifact:
    """Register a local file pointer or artifact reference."""
    art = Artifact(
        task_id=req.task_id,
        project_id=req.project_id,
        kind=req.kind,
        path_or_uri=req.path_or_uri,
        sha256=req.sha256,
        pinned=req.pinned,
        metadata_json=req.metadata_json,
    )
    db.add(art)
    db.flush()
    record_audit(
        db,
        actor=actor,
        action="artifact_registered",
        target_type="artifact",
        target_id=art.artifact_id,
        detail={"kind": art.kind, "path_or_uri": art.path_or_uri},
    )
    db.commit()
    db.refresh(art)
    return art


def list_artifacts(
    db: Session, *, task_id: str | None = None, project_id: str | None = None
) -> list[Artifact]:
    """List registered artifacts filtered by task or project."""
    stmt = select(Artifact).order_by(Artifact.created_at.desc())
    if task_id:
        stmt = stmt.where(Artifact.task_id == task_id)
    if project_id:
        stmt = stmt.where(Artifact.project_id == project_id)
    return list(db.scalars(stmt).all())
