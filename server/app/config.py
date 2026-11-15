"""Application configuration and role-scoped token registry."""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class Actor:
    """Authenticated caller identity mapped from a Bearer token."""

    role: str  # "bridge" | "agent"
    actor_id: str  # "bridge_user" | "gemini" | "deepseek" | "gpt" | custom open-source actor


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables or .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    tower_env: str = "development"
    tower_host: str = "127.0.0.1"
    tower_port: int = 8100
    database_url: str = "sqlite+pysqlite:///./tower_hub_dev.db"

    tower_bridge_token: str = ""
    tower_agent_token_gemini: str = ""
    tower_agent_token_deepseek: str = ""
    tower_agent_token_gpt: str = ""
    # Optional extensible mapping for additional open-source execution agents:
    # JSON '{"cursor":"tok1","windsurf":"tok2"}' or CSV 'cursor=tok1,windsurf=tok2'
    tower_agent_tokens: str = ""
    # Comma-separated agent IDs allowed to submit rolling thread digests alongside Bridge
    tower_summarizer_actors: str = "deepseek"

    default_lease_minutes: int = 30
    max_patch_bytes: int = 512 * 1024  # 512 KB cap for inline git diff patches

    def _extra_agent_tokens(self) -> list[tuple[str, str]]:
        raw = self.tower_agent_tokens.strip()
        if not raw:
            return []
        if raw.startswith("{"):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return [
                        (str(k).strip(), str(v).strip())
                        for k, v in parsed.items()
                        if str(k).strip() and str(v).strip()
                    ]
            except json.JSONDecodeError:
                return []
        pairs: list[tuple[str, str]] = []
        for entry in raw.split(","):
            chunk = entry.strip()
            if not chunk:
                continue
            sep = "=" if "=" in chunk else (":" if ":" in chunk else "")
            if not sep:
                continue
            actor_id, token = chunk.split(sep, 1)
            if actor_id.strip() and token.strip():
                pairs.append((actor_id.strip(), token.strip()))
        return pairs

    def summarizer_actor_set(self) -> set[str]:
        """Return actor_ids authorized to post rolling digests alongside Bridge."""
        return {x.strip() for x in self.tower_summarizer_actors.split(",") if x.strip()}

    def resolve_token(self, presented_token: str) -> Actor | None:
        """Match a Bearer token in constant time and return its Actor identity."""
        if not presented_token:
            return None

        candidates: list[tuple[str, Actor]] = [
            (self.tower_bridge_token, Actor(role="bridge", actor_id="bridge_user")),
            (self.tower_agent_token_gemini, Actor(role="agent", actor_id="gemini")),
            (self.tower_agent_token_deepseek, Actor(role="agent", actor_id="deepseek")),
            (self.tower_agent_token_gpt, Actor(role="agent", actor_id="gpt")),
        ]
        for actor_id, token in self._extra_agent_tokens():
            candidates.append((token, Actor(role="agent", actor_id=actor_id)))

        for configured, actor in candidates:
            if configured and hmac.compare_digest(presented_token, configured):
                return actor
        return None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings instance."""
    return Settings()
