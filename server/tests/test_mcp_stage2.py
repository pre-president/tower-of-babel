"""Stage 2 tests for standard Streamable HTTP /mcp server, RBAC, and extensible auth."""

from __future__ import annotations

from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import get_engine, get_session_factory
from app.main import create_app
from app.models import Base

BRIDGE_HEADERS = {"Authorization": "Bearer test-bridge-token"}
GEMINI_HEADERS = {"Authorization": "Bearer test-gemini-token"}
DEEPSEEK_HEADERS = {"Authorization": "Bearer test-deepseek-token"}
CUSTOM_CURSOR_HEADERS = {"Authorization": "Bearer test-cursor-token"}


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_file = tmp_path / "test_mcp_stage2.db"
    monkeypatch.setenv("TOWER_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_file}")
    monkeypatch.setenv("TOWER_BRIDGE_TOKEN", "test-bridge-token")
    monkeypatch.setenv("TOWER_AGENT_TOKEN_GEMINI", "test-gemini-token")
    monkeypatch.setenv("TOWER_AGENT_TOKEN_DEEPSEEK", "test-deepseek-token")
    monkeypatch.setenv("TOWER_AGENT_TOKEN_GPT", "test-gpt-token")
    monkeypatch.setenv("TOWER_AGENT_TOKENS", "cursor=test-cursor-token")

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()

    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    app = create_app()
    with TestClient(app) as tc:
        yield tc

    engine.dispose()
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()


def _rpc(
    client: TestClient,
    method: str,
    params: dict | None = None,
    *,
    headers: dict[str, str] = GEMINI_HEADERS,
    req_id: int | None = 1,
    extra_headers: dict[str, str] | None = None,
):
    payload: dict = {"jsonrpc": "2.0", "method": method}
    if req_id is not None:
        payload["id"] = req_id
    if params is not None:
        payload["params"] = params
    merged = {**headers, **(extra_headers or {})}
    return client.post("/mcp", headers=merged, json=payload)


def test_mcp_auth_lifecycle_and_discovery(client: TestClient) -> None:
    # Missing/invalid Bearer must return 401
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"}).status_code == 401
    assert client.get("/mcp").status_code == 401
    assert client.get("/mcp", headers=GEMINI_HEADERS).status_code == 405

    init_resp = _rpc(client, "initialize", {"protocolVersion": "2025-03-26"})
    assert init_resp.status_code == 200
    session_id = init_resp.headers.get("Mcp-Session-Id")
    assert session_id
    body = init_resp.json()
    assert body["result"]["protocolVersion"] == "2025-03-26"
    assert body["result"]["serverInfo"]["name"] == "tower-of-babel-hub"

    # Notification returns 202 empty body
    notif = _rpc(client, "notifications/initialized", req_id=None)
    assert notif.status_code == 202

    # Custom extensible agent token works seamlessly
    listed = _rpc(client, "tools/list", headers=CUSTOM_CURSOR_HEADERS)
    assert listed.status_code == 200
    tool_names = {t["name"] for t in listed.json()["result"]["tools"]}
    assert {
        "start_work_from_chat",
        "get_handoff_package",
        "get_checkpoint_trace",
        "get_task_context",
        "get_execution_status",
        "list_threads",
    }.issubset(tool_names)

    # SSE response negotiation
    sse_resp = _rpc(
        client,
        "ping",
        extra_headers={"Accept": "text/event-stream"},
    )
    assert sse_resp.status_code == 200
    assert sse_resp.headers["content-type"].startswith("text/event-stream")
    assert "event: message" in sse_resp.text

    assert client.delete("/mcp", headers=GEMINI_HEADERS).status_code == 204


def test_mcp_start_work_handoff_trace_and_rbac(client: TestClient) -> None:
    # Seed planner thread, 22 turns, and an APPROVED decision via Bridge REST
    assert client.post(
        "/api/v1/bridge/threads/register",
        headers=BRIDGE_HEADERS,
        json={
            "planner_thread_id": "CHAT_AR_MAIN",
            "project_id": "AR_NORTH_PACIFIC",
            "title": "北太平洋大气河主线讨论",
            "sync_enabled": True,
        },
    ).status_code == 200

    msgs = [
        {
            "role": "user" if i % 2 == 0 else "assistant",
            "content": f"Planning turn {i+1}: IVT threshold test",
            "external_message_id": f"m_{i}",
        }
        for i in range(22)
    ]
    assert client.post(
        "/api/v1/bridge/messages/sync",
        headers=BRIDGE_HEADERS,
        json={"planner_thread_id": "CHAT_AR_MAIN", "messages": msgs},
    ).status_code == 200

    assert client.post(
        "/api/v1/bridge/decisions/publish",
        headers=BRIDGE_HEADERS,
        json={
            "project_id": "AR_NORTH_PACIFIC",
            "planner_thread_id": "CHAT_AR_MAIN",
            "title": "Use IVT >= 250 kg/m/s",
            "decision_content": "Adopt IVT >= 250 for AR object detection.",
        },
    ).status_code == 200

    # Agent must NEVER approve decisions or resolve questions via MCP
    forbidden = _rpc(
        client,
        "tools/call",
        {
            "name": "approve_decision",
            "arguments": {
                "project_id": "AR_NORTH_PACIFIC",
                "planner_thread_id": "CHAT_AR_MAIN",
                "title": "Illegal Override",
                "decision_content": "Agent tries to approve",
            },
        },
        headers=GEMINI_HEADERS,
    )
    assert forbidden.status_code == 200
    assert forbidden.json()["result"]["isError"] is True
    assert "403" in forbidden.json()["result"]["content"][0]["text"]

    # Gemini starts work by matching partial Chat title over MCP
    started = _rpc(
        client,
        "tools/call",
        {
            "name": "start_work_from_chat",
            "arguments": {
                "chat_ref": "大气河主线",
                "task_title": "Compute Winter IVT Climatology",
                "goal_description": "Verify winter AR detection on ERA5",
                "session_id": "agy-sess-1",
                "chat_turn_limit": 18,
            },
        },
        headers=GEMINI_HEADERS,
    )
    assert started.status_code == 200
    s_data = started.json()["result"]["structuredContent"]
    assert started.json()["result"]["isError"] is False
    task_id = s_data["task"]["task_id"]
    assert len(s_data["context"]["recent_chat_turns"]) == 18
    assert s_data["_security_notice"].startswith("UNTRUSTED")

    # Record progress checkpoint 1, then quota_exhausted handoff with 12 execution turns
    prog = _rpc(
        client,
        "tools/call",
        {
            "name": "report_progress",
            "arguments": {
                "task_id": task_id,
                "session_id": "agy-sess-1",
                "completed_steps": ["Loaded ERA5 mask"],
                "current_step": "Running IVT flux",
                "execution_digest": "Initial mask script passed",
            },
        },
        headers=GEMINI_HEADERS,
    )
    assert prog.json()["result"]["isError"] is False

    exec_turns = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"Exec turn {i+1}"}
        for i in range(12)
    ]
    ho = _rpc(
        client,
        "tools/call",
        {
            "name": "handoff_task",
            "arguments": {
                "task_id": task_id,
                "session_id": "agy-sess-1",
                "reason": "quota_exhausted",
                "execution_digest": "IVT solver hit quota before plotting",
                "completed_steps": ["Loaded ERA5 mask", "Computed U/V IVT"],
                "current_step": "Plotting DJF frequency",
                "next_steps": ["Run pytest -k test_ivt", "Export figure script"],
                "recent_turns": exec_turns,
                "git_commit": "abc1234",
                "git_diff_patch": "--- a/ivt.py\n+++ b/ivt.py\n+threshold = 250\n",
            },
        },
        headers=GEMINI_HEADERS,
    )
    assert ho.json()["result"]["isError"] is False

    # DeepSeek claims handoff and reads both concise package & paginated trace over MCP
    claim = _rpc(
        client,
        "tools/call",
        {
            "name": "claim_handoff",
            "arguments": {"task_id": task_id, "session_id": "ds-sess-2"},
        },
        headers=DEEPSEEK_HEADERS,
    )
    assert claim.json()["result"]["structuredContent"]["status"] == "HANDED_OFF"

    pkg = _rpc(
        client,
        "tools/call",
        {"name": "get_handoff_package", "arguments": {"task_id": task_id}},
        headers=DEEPSEEK_HEADERS,
    ).json()["result"]["structuredContent"]
    assert len(pkg["recent_chat_turns"]) == 5
    assert len(pkg["previous_execution_recent_turns"]) == 10
    assert pkg["previous_execution_recent_turns"][0]["content"] == "Exec turn 3"
    assert "+threshold = 250" in pkg["git_diff_patch"]

    trace = _rpc(
        client,
        "tools/call",
        {"name": "get_checkpoint_trace", "arguments": {"task_id": task_id, "offset": 0, "limit": 5}},
        headers=DEEPSEEK_HEADERS,
    ).json()["result"]["structuredContent"]
    assert trace["total_checkpoints"] == 2
    assert len(trace["checkpoints"]) == 2

    conf = _rpc(
        client,
        "tools/call",
        {
            "name": "confirm_handoff",
            "arguments": {
                "task_id": task_id,
                "session_id": "ds-sess-2",
                "workspace_verified_note": "Branch & diff verified locally",
            },
        },
        headers=DEEPSEEK_HEADERS,
    ).json()["result"]["structuredContent"]
    assert conf["status"] == "RESUMED"

    # Also verify REST mirror endpoints
    assert client.get(f"/api/v1/hub/tasks/{task_id}/context", headers=DEEPSEEK_HEADERS).status_code == 200
    assert client.get(f"/api/v1/hub/tasks/{task_id}/trace", headers=DEEPSEEK_HEADERS).status_code == 200
    assert client.get("/api/v1/hub/execution-status", headers=DEEPSEEK_HEADERS).status_code == 200