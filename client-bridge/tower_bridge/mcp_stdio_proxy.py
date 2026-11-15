"""Zero-dependency MCP stdio proxy connecting local AI clients to Tower of Babel Hub."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

# Ensure package import works even when invoked directly via script path
_BRIDGE_ROOT = Path(__file__).resolve().parent.parent
if str(_BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_BRIDGE_ROOT))

from tower_bridge.adapters import default_registry  # noqa: E402
from tower_bridge.config import BridgeConfig, HubClient, HubHttpError  # noqa: E402
from tower_bridge.git_snapshot import capture_git_snapshot  # noqa: E402

LOCAL_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "local_workspace_status",
        "description": (
            "Inspect local Git branch, commit SHA, working tree status, and recent .jsonl session "
            "discovery before starting or confirming a task handoff."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "default": "."},
                "client": {"type": "string", "default": "auto"},
            },
        },
    },
    {
        "name": "local_inspect_execution",
        "description": (
            "Extract the latest ~10 clean visible conversation turns from local Claude Code / Codex / "
            "Antigravity .jsonl logs (filtering tool/thinking noise) plus local git diff preview."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "default": "."},
                "client": {
                    "type": "string",
                    "default": "auto",
                    "description": "auto | claude_code | codex | antigravity",
                },
                "session_file": {"type": "string"},
                "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 30},
            },
        },
    },
    {
        "name": "local_handoff_task",
        "description": (
            "Package local .jsonl latest ~10 execution turns and git diff snapshot, then submit "
            "handoff_task to the central Hub cleanly."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["task_id", "session_id"],
            "properties": {
                "task_id": {"type": "string"},
                "session_id": {"type": "string"},
                "reason": {
                    "type": "string",
                    "enum": ["manual_handoff", "quota_exhausted"],
                    "default": "manual_handoff",
                },
                "repo_path": {"type": "string", "default": "."},
                "client": {"type": "string", "default": "auto"},
                "session_file": {"type": "string"},
                "turn_limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 30},
                "execution_digest": {"type": "string", "default": ""},
                "completed_steps": {"type": "array", "items": {"type": "string"}},
                "current_step": {"type": "string", "default": ""},
                "next_steps": {"type": "array", "items": {"type": "string"}},
                "blockers": {"type": "string", "default": ""},
            },
        },
    },
]

LOCAL_TOOL_NAMES = {spec["name"] for spec in LOCAL_TOOL_SPECS}


class LocalBridgeMcpServer:
    """Routes stdio MCP JSON-RPC calls between local workspace tools and remote Hub /mcp."""

    def __init__(self, config: BridgeConfig | None = None) -> None:
        self.config = config or BridgeConfig.from_env()
        self.hub = HubClient(self.config)
        self.registry = default_registry()

    def _synthesize_digest(self, turns: list[dict[str, Any]], git_info: dict[str, Any]) -> str:
        parts = []
        if git_info.get("git_branch"):
            parts.append(f"Branch `{git_info['git_branch']}` @ {git_info.get('git_commit') or 'uncommitted'}")
        if git_info.get("status_short"):
            changed_count = len(git_info["status_short"].splitlines())
            parts.append(f"{changed_count} modified/untracked path(s)")
        for t in reversed(turns):
            if t.get("role") == "assistant" and t.get("content"):
                parts.append("Last output: " + t["content"][:400].replace("\n", " "))
                break
        return " | ".join(parts) or "Local workspace snapshot captured for handoff."

    def _call_local_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        repo_path = str(args.get("repo_path") or ".")
        client_name = str(args.get("client") or "auto")

        if name == "local_workspace_status":
            snap = capture_git_snapshot(repo_path, write_oversize_file=False)
            extracted = self.registry.extract_recent(
                client=client_name, repo_path=repo_path, limit=3
            )
            return {
                "git": {
                    "is_git_repo": snap.is_git_repo,
                    "branch": snap.git_branch,
                    "commit": snap.git_commit,
                    "status_short": snap.status_short,
                },
                "latest_session": extracted.get("session"),
                "registered_adapters": self.registry.registered_clients(),
            }

        if name == "local_inspect_execution":
            limit = int(args.get("limit") or 10)
            extracted = self.registry.extract_recent(
                client=client_name,
                repo_path=repo_path,
                session_file=args.get("session_file"),
                limit=limit,
            )
            snap = capture_git_snapshot(repo_path, write_oversize_file=False)
            return {
                "session": extracted.get("session"),
                "recent_turns": extracted.get("recent_turns", []),
                "excerpt": extracted.get("excerpt", ""),
                "git": snap.to_dict(),
            }

        if name == "local_handoff_task":
            task_id = str(args["task_id"])
            session_id = str(args["session_id"])
            turn_limit = int(args.get("turn_limit") or 10)
            extracted = self.registry.extract_recent(
                client=client_name,
                repo_path=repo_path,
                session_file=args.get("session_file"),
                limit=turn_limit,
            )
            snap = capture_git_snapshot(repo_path, task_id=task_id, write_oversize_file=True)
            turns = extracted.get("recent_turns", [])
            digest = str(args.get("execution_digest") or "").strip()
            if not digest:
                digest = self._synthesize_digest(turns, snap.to_dict())

            artifact_refs = [snap.oversized_patch_ref] if snap.oversized_patch_ref else []
            hub_payload = {
                "task_id": task_id,
                "session_id": session_id,
                "reason": str(args.get("reason") or "manual_handoff"),
                "execution_digest": digest,
                "completed_steps": args.get("completed_steps") or [],
                "current_step": str(args.get("current_step") or ""),
                "next_steps": args.get("next_steps") or [],
                "blockers": str(args.get("blockers") or ""),
                "recent_turns": [
                    {"role": t["role"], "content": t["content"], "timestamp": t.get("timestamp")}
                    for t in turns[-10:]
                ],
                "execution_trace_excerpt": extracted.get("excerpt", "")[:24000],
                "git_commit": snap.git_commit,
                "git_diff_patch": snap.git_diff_patch,
                "local_artifact_refs": artifact_refs,
            }
            ckpt = self.hub.call_tool("handoff_task", hub_payload)
            return {
                "handoff_checkpoint": ckpt,
                "captured_turns": len(turns),
                "source_session": extracted.get("session"),
                "git_branch": snap.git_branch,
                "git_commit": snap.git_commit,
            }

        raise ValueError(f"Unknown local tool '{name}'")

    def handle_rpc(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        method = msg.get("method")
        if "id" not in msg or (isinstance(method, str) and method.startswith("notifications/")):
            return None

        req_id = msg.get("id")
        params = msg.get("params") or {}

        if method == "initialize":
            try:
                remote = self.hub.mcp_rpc("initialize", params)
                protocol = remote.get("protocolVersion", "2025-03-26")
                instructions = remote.get("instructions", "")
            except HubHttpError:
                protocol = str(params.get("protocolVersion") or "2025-03-26")
                instructions = "Tower of Babel local bridge (remote hub currently unreachable)."
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": protocol,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "tower-of-babel-bridge", "version": "0.2.0"},
                    "instructions": instructions,
                },
            }

        if method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}

        if method == "tools/list":
            tools: list[dict[str, Any]] = []
            try:
                remote_res = self.hub.mcp_rpc("tools/list")
                tools.extend(remote_res.get("tools", []))
            except HubHttpError as exc:
                # Keep local tools discoverable even if hub is offline
                pass
            if self.config.enable_local_tools:
                tools.extend(LOCAL_TOOL_SPECS)
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}}

        if method == "tools/call":
            tool_name = str(params.get("name") or "")
            arguments = params.get("arguments") or {}
            try:
                if self.config.enable_local_tools and tool_name in LOCAL_TOOL_NAMES:
                    data = self._call_local_tool(tool_name, arguments)
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps(data, ensure_ascii=False, indent=2),
                                }
                            ],
                            "structuredContent": data,
                            "isError": False,
                        },
                    }
                result = self.hub.mcp_rpc("tools/call", {"name": tool_name, "arguments": arguments})
                return {"jsonrpc": "2.0", "id": req_id, "result": result}
            except Exception as exc:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": str(exc)}],
                        "isError": True,
                    },
                }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method '{method}' not found"},
        }


def main() -> int:
    server = LocalBridgeMcpServer()
    reader = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
    writer = sys.stdout.buffer

    for raw_line in reader:
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            err = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {exc}"},
            }
            writer.write((json.dumps(err, ensure_ascii=False) + "\n").encode("utf-8"))
            writer.flush()
            continue

        reply = server.handle_rpc(payload) if isinstance(payload, dict) else None
        if reply is not None:
            writer.write((json.dumps(reply, ensure_ascii=False) + "\n").encode("utf-8"))
            writer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())