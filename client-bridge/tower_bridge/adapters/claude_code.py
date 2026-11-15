"""Transcript adapter for Claude Code (~/.claude/projects/**/*.jsonl)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tower_bridge.adapters.base import (
    SessionCandidate,
    TranscriptAdapter,
    TranscriptTurn,
    coalesce_turns,
    read_head_jsonl_objects,
    read_tail_jsonl_objects,
)


def _extract_claude_text(content: Any, *, allow_tool_result_only: bool = False) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype in ("text", "input_text") and isinstance(block.get("text"), str):
            parts.append(block["text"].strip())
        elif allow_tool_result_only and btype == "tool_result":
            # Skip tool result noise cleanly
            continue
    return "\n".join(p for p in parts if p).strip()


class ClaudeCodeAdapter(TranscriptAdapter):
    """Reads visible user/assistant dialogue from Claude Code session JSONL logs."""

    client_name = "claude_code"

    def __init__(self, root_dir: Path | None = None) -> None:
        self.root_dir = root_dir or (Path.home() / ".claude" / "projects")

    def discover_sessions(self, *, repo_path: Path | None = None) -> list[SessionCandidate]:
        if not self.root_dir.is_dir():
            return []
        files = [
            p
            for p in self.root_dir.rglob("*.jsonl")
            if p.is_file() and "subagents" not in p.parts
        ]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        out: list[SessionCandidate] = []
        for f in files[:25]:
            cwd = None
            branch = None
            session_id = f.stem
            for obj in read_head_jsonl_objects(f, max_lines=25):
                if isinstance(obj.get("cwd"), str) and obj["cwd"]:
                    cwd = obj["cwd"]
                if isinstance(obj.get("gitBranch"), str) and obj["gitBranch"]:
                    branch = obj["gitBranch"]
                if isinstance(obj.get("sessionId"), str) and obj["sessionId"]:
                    session_id = obj["sessionId"]
                if cwd:
                    break
            out.append(
                SessionCandidate(
                    client=self.client_name,
                    session_id=session_id,
                    file_path=f,
                    mtime=f.stat().st_mtime,
                    cwd=cwd,
                    git_branch=branch,
                )
            )
        return out

    def parse_turns(self, file_path: Path, *, limit: int = 10) -> list[TranscriptTurn]:
        size = file_path.stat().st_size if file_path.is_file() else 0
        for window in (2_000_000, 8_000_000, size or 2_000_000):
            records = read_tail_jsonl_objects(file_path, max_bytes=window)
            turns = self._records_to_turns(records)
            coalesced = coalesce_turns(turns, limit=limit)
            if len(coalesced) >= limit or window >= size:
                return coalesced
        return []

    def _records_to_turns(self, records: list[dict[str, Any]]) -> list[TranscriptTurn]:
        turns: list[TranscriptTurn] = []
        for obj in records:
            rtype = obj.get("type")
            if rtype not in ("user", "assistant"):
                continue
            if obj.get("isSidechain") or obj.get("isMeta"):
                continue
            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue
            text = _extract_claude_text(msg.get("content"))
            if not text:
                continue
            turns.append(
                TranscriptTurn(
                    role=role,
                    content=text,
                    timestamp=obj.get("timestamp"),
                    client=self.client_name,
                    model=msg.get("model") if isinstance(msg.get("model"), str) else None,
                )
            )
        return turns