"""Configuration resolution and zero-dependency HTTP/MCP client for Tower of Babel."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def default_credentials_path(agent_name: str | None = None) -> Path:
    """Return user-local credentials path outside any Git repository."""
    base = Path.home() / ".tower-of-babel"
    if agent_name:
        return base / "agents" / f"{agent_name}.env"
    return base / "credentials.env"


def load_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE pairs from a local .env file without modifying process globals permanently."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        k = key.strip()
        v = val.strip().strip('"').strip("'")
        if k:
            values[k] = v
    return values


@dataclass(frozen=True)
class BridgeConfig:
    """Resolved connection configuration for talking to Tower of Babel Hub."""

    hub_url: str
    token: str
    agent_name: str = "agent"
    enable_local_tools: bool = True
    timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls, *, agent_hint: str | None = None) -> "BridgeConfig":
        agent = agent_hint or os.environ.get("TOWER_AGENT_NAME", "").strip() or "agent"
        file_vars: dict[str, str] = {}

        token_file_raw = os.environ.get("TOWER_TOKEN_FILE", "").strip()
        if token_file_raw:
            file_vars.update(load_env_file(Path(token_file_raw).expanduser()))
        else:
            default_cred = default_credentials_path()
            file_vars.update(load_env_file(default_cred))
            if agent != "agent":
                file_vars.update(load_env_file(default_credentials_path(agent)))

        hub_url = (
            os.environ.get("TOWER_HUB_URL", "").strip()
            or file_vars.get("TOWER_HUB_URL", "").strip()
            or "https://hub.atmosphericduct.xyz"
        ).rstrip("/")

        token = (
            os.environ.get("TOWER_TOKEN", "").strip()
            or os.environ.get("TOWER_AGENT_TOKEN", "").strip()
            or file_vars.get("TOWER_TOKEN", "").strip()
            or file_vars.get("TOWER_BRIDGE_TOKEN", "").strip()
        )
        local_flag = os.environ.get("TOWER_ENABLE_LOCAL_TOOLS", "1").strip().lower() not in (
            "0",
            "false",
            "no",
        )
        return cls(
            hub_url=hub_url,
            token=token,
            agent_name=agent,
            enable_local_tools=local_flag,
        )


class HubHttpError(RuntimeError):
    """Raised when Hub returns a non-2xx response or cannot be reached."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class HubClient:
    """Stdlib HTTP client supporting both Hub REST API and /mcp Streamable HTTP JSON-RPC."""

    def __init__(self, config: BridgeConfig) -> None:
        self.config = config
        self.mcp_session_id: str | None = None
        self._rpc_counter = 0

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "User-Agent": f"tower-of-babel-bridge/{self.config.agent_name}",
        }
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"
        if self.mcp_session_id:
            headers["Mcp-Session-Id"] = self.mcp_session_id
        if extra:
            headers.update(extra)
        return headers

    def request_json(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[Any, dict[str, str]]:
        url = f"{self.config.hub_url}{path}"
        if query:
            clean_q = {k: v for k, v in query.items() if v is not None}
            if clean_q:
                url = f"{url}?{urlencode(clean_q)}"
        raw_body = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = Request(url=url, data=raw_body, headers=self._headers(extra_headers), method=method)
        try:
            with urlopen(req, timeout=self.config.timeout_seconds) as resp:
                resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                raw = resp.read().decode("utf-8", errors="replace")
                if not raw.strip():
                    return None, resp_headers
                return json.loads(raw), resp_headers
        except HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            raise HubHttpError(exc.code, f"HTTP {exc.code} on {method} {path}: {err_body}") from exc
        except URLError as exc:
            raise HubHttpError(0, f"Network error reaching {url}: {exc.reason}") from exc

    def mcp_rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._rpc_counter += 1
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._rpc_counter,
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        data, resp_headers = self.request_json("POST", "/mcp", body=payload)
        if "mcp-session-id" in resp_headers:
            self.mcp_session_id = resp_headers["mcp-session-id"]
        if isinstance(data, dict) and "error" in data:
            err = data["error"]
            raise HubHttpError(400, f"MCP RPC error {err.get('code')}: {err.get('message')}")
        if not isinstance(data, dict):
            raise HubHttpError(500, "Unexpected empty MCP response")
        return data.get("result", {})

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self.mcp_rpc("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            content = result.get("content") or []
            msg = content[0].get("text") if content and isinstance(content[0], dict) else str(result)
            raise HubHttpError(400, msg)
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return structured
        return result