"""FastAPI application factory for Tower of Babel Central Hub."""

from __future__ import annotations

from fastapi import FastAPI

from app.api.bridge import router as bridge_router
from app.api.health import router as health_router
from app.api.hub import router as hub_router
from app.mcp.router import router as mcp_router


def create_app() -> FastAPI:
    """Create and configure the Tower of Babel FastAPI application."""
    app = FastAPI(
        title="Tower of Babel Central Hub",
        version="0.2.0",
        description="Personal Multi-Agent Collaboration Hub (Ocean University of China)",
    )
    app.include_router(health_router)
    app.include_router(bridge_router)
    app.include_router(hub_router)
    app.include_router(mcp_router)
    return app


app = create_app()
