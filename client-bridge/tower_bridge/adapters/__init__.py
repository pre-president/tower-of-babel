"""Built-in .jsonl transcript adapters registry."""

from tower_bridge.adapters.antigravity import AntigravityAdapter
from tower_bridge.adapters.base import (
    SessionCandidate,
    TranscriptAdapter,
    TranscriptAdapterRegistry,
    TranscriptTurn,
)
from tower_bridge.adapters.claude_code import ClaudeCodeAdapter
from tower_bridge.adapters.codex import CodexAdapter


def default_registry() -> TranscriptAdapterRegistry:
    """Create a TranscriptAdapterRegistry preloaded with built-in adapters."""
    reg = TranscriptAdapterRegistry()
    reg.register(ClaudeCodeAdapter())
    reg.register(CodexAdapter())
    reg.register(AntigravityAdapter())
    return reg


__all__ = [
    "AntigravityAdapter",
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "SessionCandidate",
    "TranscriptAdapter",
    "TranscriptAdapterRegistry",
    "TranscriptTurn",
    "default_registry",
]