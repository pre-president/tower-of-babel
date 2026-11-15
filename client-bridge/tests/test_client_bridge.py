"""Unit and integration tests for client-bridge adapters, git snapshot, stdio MCP proxy, and registration."""

from __future__ import annotations

import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from tower_bridge.adapters import (
    AntigravityAdapter,
    ClaudeCodeAdapter,
    CodexAdapter,
    TranscriptAdapterRegistry,
)
from tower_bridge.config import BridgeConfig
from tower_bridge.git_snapshot import capture_git_snapshot
from tower_bridge.mcp_stdio_proxy import LocalBridgeMcpServer
from tower_bridge.register_clients import register_json_client, upsert_codex_toml


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )


def test_claude_code_adapter_filters_noise_and_keeps_latest_10(tmp_path: Path) -> None:
    root = tmp_path / ".claude" / "projects" / "demo"
    log = root / "sess-1.jsonl"
    rows: list[dict] = []
    for i in range(1, 15):
        rows.append(
            {
                "type": "user",
                "cwd": str(tmp_path),
                "gitBranch": "main",
                "sessionId": "sess-1",
                "message": {"role": "user", "content": [{"type": "text", "text": f"Question {i}"}]},
            }
        )
        # Tool result + thinking + tool_use noise must be ignored
        rows.append(
            {
                "type": "user",
                "message": {"role": "user", "content": [{"type": "tool_result", "content": "raw noise"}]},
            }
        )
        rows.append(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "model": "deepseek-chat",
                    "content": [
                        {"type": "thinking", "thinking": "hidden internal chain"},
                        {"type": "tool_use", "name": "bash", "input": {"cmd": "ls"}},
                    ],
                },
            }
        )
        rows.append(
            {
                "type": "assistant",
                "isSidechain": True,
                "message": {"role": "assistant", "content": [{"type": "text", "text": "sidechain ignore"}]},
            }
        )
        rows.append(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "model": "deepseek-chat",
                    "content": [{"type": "text", "text": f"Answer {i}"}],
                },
            }
        )
    _write_jsonl(log, rows)

    adapter = ClaudeCodeAdapter(root_dir=tmp_path / ".claude" / "projects")
    sessions = adapter.discover_sessions(repo_path=tmp_path)
    assert len(sessions) == 1
    assert sessions[0].cwd == str(tmp_path)

    turns = adapter.parse_turns(log, limit=10)
    assert len(turns) == 10
    assert turns[0].content == "Question 10"
    assert turns[-1].content == "Answer 14"
    assert all("hidden internal" not in t.content and "sidechain" not in t.content for t in turns)


def test_codex_adapter_strips_env_and_reasoning(tmp_path: Path) -> None:
    log = tmp_path / ".codex" / "sessions" / "2026" / "11" / "rollout-demo.jsonl"
    rows = [
        {"type": "session_meta", "payload": {"id": "codex-1", "cwd": str(tmp_path)}},
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": "System instructions ignore me"}],
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "<environment_context><cwd>C:/demo</cwd></environment_context>\nFix ERA5 loader",
                    }
                ],
            },
        },
        {"type": "response_item", "payload": {"type": "reasoning", "summary": []}},
        {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "shell"}},
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Fixed ERA5 missing time step."}],
            },
        },
    ]
    _write_jsonl(log, rows)

    reg = TranscriptAdapterRegistry()
    reg.register(CodexAdapter(root_dir=tmp_path / ".codex" / "sessions"))
    out = reg.extract_recent(client="gpt", repo_path=tmp_path, limit=10)
    assert out["session"]["client"] == "codex"
    assert [t["role"] for t in out["recent_turns"]] == ["user", "assistant"]
    assert out["recent_turns"][0]["content"] == "Fix ERA5 loader"
    assert out["recent_turns"][1]["content"] == "Fixed ERA5 missing time step."


def test_antigravity_adapter_reads_full_when_truncated(tmp_path: Path) -> None:
    log_dir = tmp_path / ".gemini" / "antigravity" / "brain" / "conv-abc" / ".system_generated" / "logs"
    compact = log_dir / "transcript.jsonl"
    full = log_dir / "transcript_full.jsonl"

    _write_jsonl(
        compact,
        [
            {"step_index": 1, "type": "GENERIC", "content": "ignored"},
            {
                "step_index": 2,
                "type": "USER_INPUT",
                "content": "<CONTEXT_SUMMARY>old summary</CONTEXT_SUMMARY><USER_REQUEST>\nRun wind plot\n</USER_REQUEST>",
            },
            {
                "step_index": 3,
                "type": "PLANNER_RESPONSE",
                "content": "truncated...",
                "truncated_fields": ["content"],
                "thinking": "internal thoughts",
            },
        ],
    )
    _write_jsonl(
        full,
        [
            {"step_index": 3, "type": "PLANNER_RESPONSE", "content": "Complete wind plot generated and verified."},
        ],
    )

    adapter = AntigravityAdapter(root_dir=tmp_path / ".gemini" / "antigravity" / "brain")
    turns = adapter.parse_turns(compact, limit=10)
    assert [t.role for t in turns] == ["user", "assistant"]
    assert turns[0].content == "Run wind plot"
    assert turns[1].content == "Complete wind plot generated and verified."


def test_git_snapshot_and_oversized_patch(tmp_path: Path) -> None:
    assert capture_git_snapshot(tmp_path).is_git_repo is False

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    f = tmp_path / "calc.py"
    f.write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "calc.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)

    f.write_text("x = 2\n", encoding="utf-8")
    snap = capture_git_snapshot(tmp_path, max_inline_bytes=1024)
    assert snap.is_git_repo is True
    assert snap.git_commit
    assert "+x = 2" in (snap.git_diff_patch or "")

    # Force oversized spill to .git/
    f.write_text("\n".join(f"line_{i} = {i}" for i in range(400)), encoding="utf-8")
    big = capture_git_snapshot(tmp_path, task_id="task_99", max_inline_bytes=200, write_oversize_file=True)
    assert big.oversized_patch_ref is not None
    assert Path(big.oversized_patch_ref["path"]).is_file()
    assert "[TRUNCATED]" in (big.git_diff_patch or "")


def test_mcp_stdio_proxy_against_http_stub(tmp_path: Path) -> None:
    captured_calls: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            req = json.loads(self.rfile.read(length).decode("utf-8"))
            captured_calls.append(req)
            method = req["method"]
            if method == "initialize":
                res = {"protocolVersion": "2025-03-26", "instructions": "Hub ready"}
            elif method == "tools/list":
                res = {"tools": [{"name": "get_handoff_package", "inputSchema": {"type": "object"}}]}
            elif method == "tools/call":
                res = {
                    "content": [{"type": "text", "text": "{}"}],
                    "structuredContent": {"checkpoint_id": "ckpt_demo", "args": req["params"]["arguments"]},
                    "isError": False,
                }
            else:
                res = {}
            body = json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": res}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Mcp-Session-Id", "sess-http-1")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:  # noqa: D401
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()

    try:
        # Seed a local Claude session inside tmp_path
        _write_jsonl(
            tmp_path / ".claude" / "projects" / "p" / "s1.jsonl",
            [
                {
                    "type": "user",
                    "cwd": str(tmp_path),
                    "message": {"role": "user", "content": [{"type": "text", "text": "Compute AR"}]},
                },
                {
                    "type": "assistant",
                    "message": {"role": "assistant", "content": [{"type": "text", "text": "Done AR"}]},
                },
            ],
        )
        cfg = BridgeConfig(hub_url=f"http://127.0.0.1:{port}", token="tok", agent_name="deepseek")
        proxy = LocalBridgeMcpServer(cfg)
        proxy.registry = TranscriptAdapterRegistry()
        proxy.registry.register(ClaudeCodeAdapter(root_dir=tmp_path / ".claude" / "projects"))

        init_reply = proxy.handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        assert init_reply is not None
        assert init_reply["result"]["protocolVersion"] == "2025-03-26"

        listed = proxy.handle_rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in listed["result"]["tools"]}
        assert {"get_handoff_package", "local_inspect_execution", "local_handoff_task"}.issubset(names)

        ho = proxy.handle_rpc(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "local_handoff_task",
                    "arguments": {
                        "task_id": "task_1",
                        "session_id": "s-1",
                        "repo_path": str(tmp_path),
                        "client": "claude_code",
                    },
                },
            }
        )
        assert ho is not None
        assert ho["result"]["isError"] is False
        assert ho["result"]["structuredContent"]["captured_turns"] == 2
    finally:
        server.shutdown()
        server.server_close()


def test_register_clients_json_and_toml_idempotent(tmp_path: Path) -> None:
    json_cfg = tmp_path / "mcp_config.json"
    json_cfg.write_text('{"mcpServers": {"other": {"command": "x"}}}', encoding="utf-8")
    register_json_client(
        json_cfg,
        server_name="tower-of-babel",
        entry={"command": "python", "args": ["proxy.py"], "env": {"TOWER_AGENT_NAME": "gemini"}},
        dry_run=False,
    )
    parsed = json.loads(json_cfg.read_text(encoding="utf-8"))
    assert "other" in parsed["mcpServers"]
    assert "tower-of-babel" in parsed["mcpServers"]

    toml_cfg = tmp_path / "config.toml"
    toml_cfg.write_text('[mcp_servers.node_repl]\ncommand = "node"\n', encoding="utf-8")
    upsert_codex_toml(
        toml_cfg,
        server_name="tower_of_babel",
        entry={"command": "python", "args": ["proxy.py"], "env": {"TOWER_AGENT_NAME": "gpt"}},
        dry_run=False,
    )
    upsert_codex_toml(
        toml_cfg,
        server_name="tower_of_babel",
        entry={"command": "python2", "args": ["proxy.py"], "env": {"TOWER_AGENT_NAME": "gpt"}},
        dry_run=False,
    )
    txt = toml_cfg.read_text(encoding="utf-8")
    assert "[mcp_servers.node_repl]" in txt
    assert txt.count("[mcp_servers.tower_of_babel]") == 1
    assert 'command = "python2"' in txt