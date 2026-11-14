"""SQLAlchemy database engine and session management."""

from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def build_engine(database_url: str) -> Engine:
    """Create a SQLAlchemy engine tuned for either PostgreSQL or SQLite."""
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    pool_kwargs = (
        {}
        if database_url.startswith("sqlite")
        else {"pool_pre_ping": True, "pool_size": 5, "max_overflow": 5}
    )
    return create_engine(database_url, connect_args=connect_args, **pool_kwargs)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return the singleton engine configured from settings."""
    return build_engine(get_settings().database_url)


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    """Return the singleton session factory."""
    return sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """Yield a request-scoped SQLAlchemy session."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
