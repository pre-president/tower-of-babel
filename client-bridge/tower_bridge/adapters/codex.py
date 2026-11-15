"""Transcript adapter for Codex (~/.codex/sessions/**/rollout-*.jsonl)."""

from __future__ import annotations

import re
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

_ENV_CTX_RE = re.compile(r"<environment_context>.*?</environment_context>", re.DOTALL)
_PERM_RE = re.compile(r"<permissions instructions>.*?</permissions instructions>", re.DOTALL)


def _clean_codex_text(content: Any) -> str:
    if isinstance(content, str):
        raw = content
    elif isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") in ("input_text", "output_text", "text") and isinstance(
                block.get("text"), str
            ):
                parts.append(block["text"])
        raw = "\n".join(parts)
    else:
        return ""
    cleaned = _ENV_CTX_RE.sub("", raw)
    cleaned = _PERM_RE.sub("", cleaned)
    return cleaned.strip()


class CodexAdapter(TranscriptAdapter):
    """Reads visible user/assistant dialogue from Codex rollout JSONL logs."""

    client_name = "codex"

    def __init__(self, root_dir: Path | None = None) -> None:
        self.root_dir = root_dir or (Path.home() / ".codex" / "sessions")

    def discover_sessions(self, *, repo_path: Path | None = None) -> list[SessionCandidate]:
        if not self.root_dir.is_dir():
            return []
        files = [p for p in self.root_dir.rglob("rollout-*.jsonl") if p.is_file()]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        out: list[SessionCandidate] = []
        for f in files[:25]:
            cwd = None
            session_id = f.stem
            for obj in read_head_jsonl_objects(f, max_lines=20):
                if obj.get("type") in ("session_meta", "turn_context"):
                    payload = obj.get("payload") or {}
                    if isinstance(payload, dict):
                        cwd = payload.get("cwd") or cwd
                        session_id = payload.get("session_id") or payload.get("id") or session_id
                if cwd:
                    break
            out.append(
                SessionCandidate(
                    client=self.client_name,
                    session_id=str(session_id),
                    file_path=f,
                    mtime=f.stat().st_mtime,
                    cwd=cwd,
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
            if obj.get("type") != "response_item":
                continue
            payload = obj.get("payload")
            if not isinstance(payload, dict) or payload.get("type") != "message":
                continue
            role = payload.get("role")
            if role not in ("user", "assistant"):
                continue
            text = _clean_codex_text(payload.get("content"))
            if not text:
                continue
            turns.append(
                TranscriptTurn(
                    role=role,
                    content=text,
                    timestamp=obj.get("timestamp"),
                    client=self.client_name,
                )
            )
        return turns