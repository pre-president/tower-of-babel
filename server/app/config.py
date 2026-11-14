"""Application configuration and role-scoped token registry."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class Actor:
    """Authenticated caller identity mapped from a Bearer token."""

    role: str  # "bridge" | "agent"
    actor_id: str  # "bridge_user" | "gemini" | "deepseek" | "gpt"


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

    default_lease_minutes: int = 30
    max_patch_bytes: int = 512 * 1024  # 512 KB cap for inline git diff patches

    def resolve_token(self, presented_token: str) -> Actor | None:
        """Match a Bearer token in constant time and return its Actor identity."""
        if not presented_token:
            return None

        candidates: tuple[tuple[str, Actor], ...] = (
            (self.tower_bridge_token, Actor(role="bridge", actor_id="bridge_user")),
            (self.tower_agent_token_gemini, Actor(role="agent", actor_id="gemini")),
            (self.tower_agent_token_deepseek, Actor(role="agent", actor_id="deepseek")),
            (self.tower_agent_token_gpt, Actor(role="agent", actor_id="gpt")),
        )
        for configured, actor in candidates:
            if configured and hmac.compare_digest(presented_token, configured):
                return actor
        return None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings instance."""
    return Settings()
