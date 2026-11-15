"""Transcript adapter for Antigravity IDE (~/.gemini/antigravity/brain/**/transcript.jsonl)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tower_bridge.adapters.base import (
    SessionCandidate,
    TranscriptAdapter,
    TranscriptTurn,
    coalesce_turns,
    read_tail_jsonl_objects,
)

_USER_REQ_RE = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)
_SUMMARY_BLOCK_RE = re.compile(r"<CONTEXT_SUMMARY>.*?</CONTEXT_SUMMARY>", re.DOTALL)
_META_BLOCK_RE = re.compile(r"<ADDITIONAL_METADATA>.*?</ADDITIONAL_METADATA>", re.DOTALL)


def _clean_antigravity_input(text: str) -> str:
    m = _USER_REQ_RE.search(text)
    if m:
        return m.group(1).strip()
    cleaned = _SUMMARY_BLOCK_RE.sub("", text)
    cleaned = _META_BLOCK_RE.sub("", cleaned)
    return cleaned.strip()


class AntigravityAdapter(TranscriptAdapter):
    """Reads visible USER_INPUT and PLANNER_RESPONSE text from Antigravity logs."""

    client_name = "antigravity"

    def __init__(self, root_dir: Path | None = None) -> None:
        self.root_dir = root_dir or (Path.home() / ".gemini" / "antigravity" / "brain")

    def discover_sessions(self, *, repo_path: Path | None = None) -> list[SessionCandidate]:
        if not self.root_dir.is_dir():
            return []
        files = [
            p
            for p in self.root_dir.rglob("transcript.jsonl")
            if p.is_file() and ".system_generated" in p.parts
        ]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        out: list[SessionCandidate] = []
        for f in files[:25]:
            # brain/<conversation_id>/.system_generated/logs/transcript.jsonl
            conv_id = f.parent.parent.parent.name
            out.append(
                SessionCandidate(
                    client=self.client_name,
                    session_id=conv_id,
                    file_path=f,
                    mtime=f.stat().st_mtime,
                    cwd=None,
                )
            )
        return out

    def parse_turns(self, file_path: Path, *, limit: int = 10) -> list[TranscriptTurn]:
        size = file_path.stat().st_size if file_path.is_file() else 0
        full_file = file_path.parent / "transcript_full.jsonl"

        for window in (1_500_000, 6_000_000, size or 1_500_000):
            records = read_tail_jsonl_objects(file_path, max_bytes=window)
            needed_full_steps = {
                obj.get("step_index")
                for obj in records
                if "content" in (obj.get("truncated_fields") or [])
                and obj.get("type") in ("USER_INPUT", "PLANNER_RESPONSE")
            }
            full_map: dict[Any, str] = {}
            if needed_full_steps and full_file.is_file():
                for full_obj in read_tail_jsonl_objects(full_file, max_bytes=window * 3):
                    si = full_obj.get("step_index")
                    if si in needed_full_steps and isinstance(full_obj.get("content"), str):
                        full_map[si] = full_obj["content"]

            turns = self._records_to_turns(records, full_map=full_map)
            coalesced = coalesce_turns(turns, limit=limit)
            if len(coalesced) >= limit or window >= size:
                return coalesced
        return []

    def _records_to_turns(
        self, records: list[dict[str, Any]], *, full_map: dict[Any, str]
    ) -> list[TranscriptTurn]:
        turns: list[TranscriptTurn] = []
        for obj in records:
            stype = obj.get("type")
            if stype not in ("USER_INPUT", "PLANNER_RESPONSE"):
                continue
            raw_content = full_map.get(obj.get("step_index"), obj.get("content"))
            if not isinstance(raw_content, str) or not raw_content.strip():
                continue

            if stype == "USER_INPUT":
                role = "user"
                text = _clean_antigravity_input(raw_content)
            else:
                role = "assistant"
                text = raw_content.strip()

            if not text:
                continue
            turns.append(
                TranscriptTurn(
                    role=role,
                    content=text,
                    timestamp=obj.get("created_at"),
                    client=self.client_name,
                    model=obj.get("model") if isinstance(obj.get("model"), str) else None,
                )
            )
        return turns