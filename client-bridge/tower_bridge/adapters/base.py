"""Pluggable .jsonl transcript adapter abstractions and fast tail-reading helpers."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TranscriptTurn:
    """Normalized visible dialogue turn extracted from an execution agent log."""

    role: str  # "user" | "assistant"
    content: str
    timestamp: str | None = None
    client: str = ""
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "client": self.client,
            "model": self.model,
        }


@dataclass(frozen=True)
class SessionCandidate:
    """Discovered local agent execution session log."""

    client: str
    session_id: str
    file_path: Path
    mtime: float
    cwd: str | None = None
    git_branch: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "client": self.client,
            "session_id": self.session_id,
            "file_path": str(self.file_path),
            "cwd": self.cwd,
            "git_branch": self.git_branch,
        }


def clip_turn_text(text: str, max_chars: int = 8000) -> str:
    """Bound individual oversized turns while keeping both beginning and latest outcome."""
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    head = max_chars // 2
    tail = max_chars - head - 32
    return f"{cleaned[:head]}\n... [trimmed middle] ...\n{cleaned[-tail:]}"


def coalesce_turns(
    turns: list[TranscriptTurn], *, limit: int = 10, max_turn_chars: int = 8000
) -> list[TranscriptTurn]:
    """Merge consecutive same-role text chunks and keep only the latest N visible turns."""
    merged: list[TranscriptTurn] = []
    for t in turns:
        text = t.content.strip()
        if not text:
            continue
        if merged and merged[-1].role == t.role:
            prev = merged[-1]
            combined = f"{prev.content}\n\n{text}"
            merged[-1] = TranscriptTurn(
                role=t.role,
                content=clip_turn_text(combined, max_turn_chars),
                timestamp=t.timestamp or prev.timestamp,
                client=t.client or prev.client,
                model=t.model or prev.model,
            )
        else:
            merged.append(
                TranscriptTurn(
                    role=t.role,
                    content=clip_turn_text(text, max_turn_chars),
                    timestamp=t.timestamp,
                    client=t.client,
                    model=t.model,
                )
            )
    return merged[-limit:]


def read_tail_jsonl_objects(path: Path, max_bytes: int = 2_000_000) -> list[dict[str, Any]]:
    """Read and parse JSONL records from the tail of a potentially huge log file."""
    if not path.is_file():
        return []
    size = path.stat().st_size
    start = max(0, size - max_bytes)
    with path.open("rb") as fh:
        if start > 0:
            fh.seek(start)
        raw = fh.read()
    lines = raw.decode("utf-8", errors="replace").splitlines()
    if start > 0 and lines:
        lines = lines[1:]  # Drop partial first line after mid-file seek

    objects: list[dict[str, Any]] = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            objects.append(obj)
    return objects


def read_head_jsonl_objects(path: Path, max_lines: int = 40) -> list[dict[str, Any]]:
    """Read the first few JSONL lines (useful for fast metadata lookup on multi-MB logs)."""
    if not path.is_file():
        return []
    objects: list[dict[str, Any]] = []
    with path.open("rb") as fh:
        for _ in range(max_lines):
            raw_line = fh.readline()
            if not raw_line:
                break
            try:
                obj = json.loads(raw_line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                objects.append(obj)
    return objects


class TranscriptAdapter(ABC):
    """Abstract base class for pluggable execution-client .jsonl transcript readers."""

    client_name: str

    @abstractmethod
    def discover_sessions(self, *, repo_path: Path | None = None) -> list[SessionCandidate]:
        """Return candidate sessions sorted newest-first."""

    @abstractmethod
    def parse_turns(self, file_path: Path, *, limit: int = 10) -> list[TranscriptTurn]:
        """Extract latest visible user/assistant turns without tool JSON or thinking noise."""


def _path_matches_repo(cwd: str | None, repo_path: Path | None) -> bool:
    if not cwd or repo_path is None:
        return False
    try:
        c = Path(cwd).resolve()
        r = repo_path.resolve()
        return c == r or r in c.parents or c in r.parents
    except Exception:
        return False


class TranscriptAdapterRegistry:
    """Extensible registry of .jsonl adapters across local coding agents."""

    def __init__(self) -> None:
        self._adapters: dict[str, TranscriptAdapter] = {}

    def register(self, adapter: TranscriptAdapter) -> None:
        self._adapters[adapter.client_name] = adapter

    def registered_clients(self) -> list[str]:
        return list(self._adapters.keys())

    def resolve_client_name(self, name: str) -> str:
        aliases = {
            "claude": "claude_code",
            "claude-code": "claude_code",
            "claude_code": "claude_code",
            "deepseek": "claude_code",
            "codex": "codex",
            "gpt": "codex",
            "antigravity": "antigravity",
            "agy": "antigravity",
            "gemini": "antigravity",
        }
        return aliases.get(name.strip().lower(), name.strip().lower())

    def extract_recent(
        self,
        *,
        client: str = "auto",
        repo_path: str | Path | None = None,
        session_file: str | Path | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Locate the best matching local session and extract latest ~N visible turns."""
        repo = Path(repo_path).expanduser().resolve() if repo_path else None

        if session_file:
            explicit = Path(session_file).expanduser().resolve()
            if not explicit.is_file():
                raise FileNotFoundError(f"Session file not found: {explicit}")
            target_client = self.resolve_client_name(client)
            if target_client == "auto":
                target_client = self._infer_client_from_path(explicit)
            adapter = self._adapters.get(target_client)
            if adapter is None:
                raise ValueError(f"No transcript adapter registered for '{target_client}'")
            turns = adapter.parse_turns(explicit, limit=limit)
            candidate = SessionCandidate(
                client=adapter.client_name,
                session_id=explicit.stem,
                file_path=explicit,
                mtime=explicit.stat().st_mtime,
            )
            return {
                "session": candidate.to_dict(),
                "recent_turns": [t.to_dict() for t in turns],
                "excerpt": self.format_excerpt(turns),
            }

        target = self.resolve_client_name(client)
        adapters = (
            list(self._adapters.values())
            if target == "auto"
            else ([self._adapters[target]] if target in self._adapters else [])
        )
        if not adapters:
            raise ValueError(f"Unknown client adapter '{client}'. Available: {self.registered_clients()}")

        candidates: list[SessionCandidate] = []
        for adapter in adapters:
            candidates.extend(adapter.discover_sessions(repo_path=repo))

        if not candidates:
            return {"session": None, "recent_turns": [], "excerpt": ""}

        # Prefer sessions whose cwd matches repo_path, breaking ties by newest mtime
        repo_matched = [c for c in candidates if _path_matches_repo(c.cwd, repo)]
        pool = repo_matched if repo_matched else candidates
        pool.sort(key=lambda c: c.mtime, reverse=True)
        chosen = pool[0]

        turns = self._adapters[chosen.client].parse_turns(chosen.file_path, limit=limit)
        return {
            "session": chosen.to_dict(),
            "recent_turns": [t.to_dict() for t in turns],
            "excerpt": self.format_excerpt(turns),
        }

    def _infer_client_from_path(self, path: Path) -> str:
        text = str(path).lower()
        if ".claude" in text:
            return "claude_code"
        if ".codex" in text:
            return "codex"
        if ".gemini" in text:
            return "antigravity"
        return next(iter(self._adapters))

    @staticmethod
    def format_excerpt(turns: list[TranscriptTurn]) -> str:
        return "\n\n".join(f"[{t.role.upper()}]\n{t.content}" for t in turns)