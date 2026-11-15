"""Safely register Tower of Babel MCP server in Antigravity, Claude Code, and Codex."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path


def _backup(path: Path) -> Path | None:
    if not path.is_file() or path.stat().st_size == 0:
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = path.with_name(f"{path.name}.bak-{stamp}")
    shutil.copy2(path, bak)
    return bak


def _load_json_dict(path: Path) -> dict:
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def build_mcp_entry(
    python_exe: str,
    proxy_script: str,
    *,
    agent_name: str,
    token_file: str,
    hub_url: str,
    include_type: bool = False,
) -> dict:
    obj: dict = {}
    if include_type:
        obj["type"] = "stdio"
    obj.update(
        {
            "command": python_exe,
            "args": [proxy_script],
            "env": {
                "TOWER_HUB_URL": hub_url,
                "TOWER_AGENT_NAME": agent_name,
                "TOWER_TOKEN_FILE": token_file,
                "TOWER_ENABLE_LOCAL_TOOLS": "1",
            },
        }
    )
    return obj


def register_json_client(
    config_path: Path,
    *,
    server_name: str,
    entry: dict,
    dry_run: bool,
) -> str:
    data = _load_json_dict(config_path)
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        servers = {}
        data["mcpServers"] = servers
    servers[server_name] = entry
    if dry_run:
        return f"[DRY-RUN] Would update {config_path} ({server_name})"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    bak = _backup(config_path)
    config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return f"Updated {config_path}" + (f" (backup: {bak.name})" if bak else "")


def _toml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def upsert_codex_toml(
    config_path: Path,
    *,
    server_name: str,
    entry: dict,
    dry_run: bool,
) -> str:
    """Idempotently replace or append [mcp_servers.<name>] in Codex config.toml."""
    existing = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
    table_key = server_name.replace("-", "_")
    block = "\n".join(
        [
            f"[mcp_servers.{table_key}]",
            f"command = {_toml_quote(entry['command'])}",
            f"args = [{', '.join(_toml_quote(a) for a in entry['args'])}]",
            "",
            f"[mcp_servers.{table_key}.env]",
            *[f"{k} = {_toml_quote(str(v))}" for k, v in entry["env"].items()],
        ]
    )

    pattern = re.compile(
        rf"(?ms)^\[mcp_servers\.{re.escape(table_key)}(?:\.[^\]]+)?\]\r?\n.*?(?=^\[|\Z)"
    )
    stripped = pattern.sub("", existing).rstrip()
    new_text = (stripped + "\n\n" + block + "\n") if stripped else (block + "\n")

    if dry_run:
        return f"[DRY-RUN] Would update {config_path} ([mcp_servers.{table_key}])"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    bak = _backup(config_path)
    config_path.write_text(new_text, encoding="utf-8")
    return f"Updated {config_path}" + (f" (backup: {bak.name})" if bak else "")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Register Tower of Babel MCP into local AI clients")
    p.add_argument("--hub-url", default="https://hub.atmosphericduct.xyz")
    p.add_argument("--python-exe", default=sys.executable)
    p.add_argument("--server-name", default="tower-of-babel")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    proxy_script = str(Path(__file__).resolve().parent / "mcp_stdio_proxy.py")
    home = Path.home()
    cred_base = home / ".tower-of-babel" / "agents"

    print(
        register_json_client(
            home / ".gemini" / "config" / "mcp_config.json",
            server_name=args.server_name,
            entry=build_mcp_entry(
                args.python_exe,
                proxy_script,
                agent_name="gemini",
                token_file=str(cred_base / "gemini.env"),
                hub_url=args.hub_url,
            ),
            dry_run=args.dry_run,
        )
    )
    print(
        register_json_client(
            home / ".claude.json",
            server_name=args.server_name,
            entry=build_mcp_entry(
                args.python_exe,
                proxy_script,
                agent_name="deepseek",
                token_file=str(cred_base / "deepseek.env"),
                hub_url=args.hub_url,
                include_type=True,
            ),
            dry_run=args.dry_run,
        )
    )
    print(
        upsert_codex_toml(
            home / ".codex" / "config.toml",
            server_name="tower_of_babel",
            entry=build_mcp_entry(
                args.python_exe,
                proxy_script,
                agent_name="gpt",
                token_file=str(cred_base / "gpt.env"),
                hub_url=args.hub_url,
            ),
            dry_run=args.dry_run,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())