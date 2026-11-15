"""Standard MCP (Streamable HTTP) server mounted inside Tower of Babel Central Hub."""

from app.mcp.router import router

__all__ = ["router"]