"""Comprehensive Stage 1 unit & integration tests for Tower of Babel Central Hub."""

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
GPT_HEADERS = {"Authorization": "Bearer test-gpt-token"}


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Provide an isolated TestClient backed by a fresh per-test SQLite database."""
    db_file = tmp_path / "test_hub.db"
    monkeypatch.setenv("TOWER_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_file}")
    monkeypatch.setenv("TOWER_BRIDGE_TOKEN", "test-bridge-token")
    monkeypatch.setenv("TOWER_AGENT_TOKEN_GEMINI", "test-gemini-token")
    monkeypatch.setenv("TOWER_AGENT_TOKEN_DEEPSEEK", "test-deepseek-token")
    monkeypatch.setenv("TOWER_AGENT_TOKEN_GPT", "test-gpt-token")

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


def _seed_thread(client: TestClient, thread_id: str = "CHAT_AR_MAIN", title: str = "南海大气波导主线") -> None:
    resp = client.post(
        "/api/v1/bridge/threads/register",
        headers=BRIDGE_HEADERS,
        json={
            "planner_thread_id": thread_id,
            "project_id": "AR_PROJECT",
            "title": title,
            "role_description": "主科研规划与方案拍板",
            "chatgpt_conversation_id": f"conv-{thread_id.lower()}",
            "sync_enabled": True,
            "auto_send_enabled": False,
        },
    )
    assert resp.status_code == 200, resp.text


def test_health_and_auth_guards(client: TestClient) -> None:
    assert client.get("/health").json()["database"] == "connected"
    assert client.get("/api/v1/hub/tasks/pending").status_code == 401
    assert (
        client.get(
            "/api/v1/hub/tasks/pending",
            headers={"Authorization": "Bearer wrong-token"},
        ).status_code
        == 401
    )

    # Agents must be physically blocked (403) from Bridge endpoints
    resp = client.post(
        "/api/v1/bridge/threads/register",
        headers=GEMINI_HEADERS,
        json={
            "planner_thread_id": "CHAT_ILLEGAL",
            "project_id": "AR",
            "title": "Illegal",
        },
    )
    assert resp.status_code == 403

    approve_attempt = client.post(
        "/api/v1/bridge/decisions/publish",
        headers=DEEPSEEK_HEADERS,
        json={
            "project_id": "AR",
            "planner_thread_id": "CHAT_AR_MAIN",
            "title": "Bypass",
            "decision_content": "Should fail",
        },
    )
    assert approve_attempt.status_code == 403


def test_idempotent_sync_and_search(client: TestClient) -> None:
    _seed_thread(client)
    batch = {
        "planner_thread_id": "CHAT_AR_MAIN",
        "source_platform": "chatgpt",
        "messages": [
            {"role": "user", "content": "请帮我确定 ERA5 大气波导高度层筛选阈值。", "external_message_id": "m1"},
            {"role": "assistant", "content": "建议采用 500m 以下梯度法与改进修正折射率判据。", "external_message_id": "m2"},
        ],
    }
    first = client.post("/api/v1/bridge/messages/sync", headers=BRIDGE_HEADERS, json=batch)
    assert first.status_code == 200
    assert first.json()["inserted_count"] == 2
    assert first.json()["duplicate_count"] == 0

    # Re-sending identical turns must be deduplicated cleanly
    second = client.post("/api/v1/bridge/messages/sync", headers=BRIDGE_HEADERS, json=batch)
    assert second.status_code == 200
    assert second.json()["inserted_count"] == 0
    assert second.json()["duplicate_count"] == 2

    # Search returns historical dialogue tagged untrusted
    hits = client.get(
        "/api/v1/hub/dialogues/search",
        headers=GEMINI_HEADERS,
        params={"q": "修正折射率", "planner_thread_id": "CHAT_AR_MAIN"},
    )
    assert hits.status_code == 200
    data = hits.json()
    assert len(data) == 1
    assert data[0]["untrusted_historical_dialogue"] is True


def test_decision_superseding_and_start_work_by_chat_name(client: TestClient) -> None:
    _seed_thread(client)

    # Seed 18 turns so we verify 15-20 fresh-start slice
    msgs = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"Turn {i+1} 讨论内容", "external_message_id": f"t{i}"}
        for i in range(18)
    ]
    assert client.post(
        "/api/v1/bridge/messages/sync",
        headers=BRIDGE_HEADERS,
        json={"planner_thread_id": "CHAT_AR_MAIN", "messages": msgs},
    ).status_code == 200

    # Agent proposes v1, Bridge approves v1
    prop = client.post(
        "/api/v1/hub/decisions/propose",
        headers=DEEPSEEK_HEADERS,
        json={
            "project_id": "AR_PROJECT",
            "planner_thread_id": "CHAT_AR_MAIN",
            "title": "垂直插值方法",
            "decision_content": "第一版采用线性插值",
            "rationale": "实现最快",
        },
    )
    assert prop.status_code == 200
    dec_v1_id = prop.json()["decision_id"]

    app_v1 = client.post(
        "/api/v1/bridge/decisions/publish",
        headers=BRIDGE_HEADERS,
        json={"decision_id": dec_v1_id},
    )
    assert app_v1.status_code == 200
    assert app_v1.json()["status"] == "APPROVED"

    # Supersede v1 with v2 directly by Bridge
    app_v2 = client.post(
        "/api/v1/bridge/decisions/publish",
        headers=BRIDGE_HEADERS,
        json={
            "project_id": "AR_PROJECT",
            "planner_thread_id": "CHAT_AR_MAIN",
            "title": "垂直插值方法",
            "decision_content": "升级为三次样条插值以消除边界尖峰",
            "rationale": "对比实验误差降低 18%",
            "rejected_alternatives": "线性插值（第一版）",
            "supersedes_decision_id": dec_v1_id,
        },
    )
    assert app_v2.status_code == 200
    v2 = app_v2.json()
    assert v2["status"] == "APPROVED"
    assert v2["decision_version"] == 2

    # Active decision list returns ONLY v2 + notice that v1 was superseded
    active_list = client.get(
        "/api/v1/hub/decisions",
        headers=GEMINI_HEADERS,
        params={"project_id": "AR_PROJECT", "active_only": True},
    ).json()
    assert len(active_list["decisions"]) == 1
    assert active_list["decisions"][0]["decision_id"] == v2["decision_id"]
    assert active_list["superseded_notice"][0]["superseded_decision_id"] == dec_v1_id

    # Kick off work by Chat Chinese title directly!
    kickoff = client.post(
        "/api/v1/hub/work/start",
        headers=GEMINI_HEADERS,
        json={
            "chat_ref": "南海大气波导主线",
            "task_title": "实现三次样条插值脚本",
            "goal_description": "重写垂直插值模块并输出对比统计",
            "session_id": "gemini-sess-01",
            "local_repo_path": "C:/Projects/AR",
            "git_branch": "feat/cubic-spline",
            "chat_turn_limit": 16,
        },
    )
    assert kickoff.status_code == 200
    payload = kickoff.json()
    assert payload["task"]["status"] == "EXECUTING"
    assert payload["task"]["assigned_agent"] == "gemini"
    assert len(payload["context"]["recent_chat_turns"]) == 16
    assert len(payload["context"]["active_decisions"]) == 1


def test_lease_conflict_handoff_latest_10_turns_and_question_unblock(client: TestClient) -> None:
    _seed_thread(client)
    # Seed 8 ChatGPT turns (handoff must return ~5)
    chat_msgs = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"Chat规划第{i+1}轮", "external_message_id": f"c{i}"}
        for i in range(8)
    ]
    client.post(
        "/api/v1/bridge/messages/sync",
        headers=BRIDGE_HEADERS,
        json={"planner_thread_id": "CHAT_AR_MAIN", "messages": chat_msgs},
    )

    # GPT starts work
    started = client.post(
        "/api/v1/hub/work/start",
        headers=GPT_HEADERS,
        json={
            "chat_ref": "CHAT_AR_MAIN",
            "task_title": "计算北太平洋 AR IVT 通量",
            "goal_description": "完成通量积分并校验",
            "session_id": "codex-sess-1",
        },
    ).json()
    task_id = started["task"]["task_id"]

    # Another agent cannot steal active lease
    conflict = client.post(
        f"/api/v1/hub/tasks/{task_id}/claim",
        headers=DEEPSEEK_HEADERS,
        json={"session_id": "ds-sess-1"},
    )
    assert conflict.status_code == 409

    # GPT asks a question -> task moves to WAITING_FOR_DECISION
    q_resp = client.post(
        "/api/v1/hub/questions",
        headers=GPT_HEADERS,
        json={
            "task_id": task_id,
            "question_text": "IVT 积分上限采用 300hPa 还是 200hPa？",
            "evidence_context": "200hPa 水汽极少但与前人文献一致",
        },
    )
    assert q_resp.status_code == 200
    qid = q_resp.json()["question_id"]

    inbox = client.get("/api/v1/bridge/threads/CHAT_AR_MAIN/inbox", headers=BRIDGE_HEADERS).json()
    assert len(inbox["pending_questions"]) == 1

    # Bridge resolves question -> task returns to EXECUTING
    res = client.post(
        f"/api/v1/bridge/questions/{qid}/resolve",
        headers=BRIDGE_HEADERS,
        json={"answer_text": "采用 300hPa，与组会方案保持一致"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "RESOLVED"

    # GPT hits quota and initiates handoff with 12 execution turns
    exec_turns = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"GPT执行轮次 {i+1}"}
        for i in range(12)
    ]
    handoff = client.post(
        f"/api/v1/hub/tasks/{task_id}/handoff",
        headers=GPT_HEADERS,
        json={
            "session_id": "codex-sess-1",
            "reason": "quota_exhausted",
            "execution_digest": "已完成数据读取与 300hPa 积分公式修改，待跑全量验证",
            "completed_steps": ["读取 ERA5", "更新积分上限至 300hPa"],
            "current_step": "准备运行 pytest_ivt.py",
            "next_steps": ["运行 pytest_ivt.py 并对比结果"],
            "recent_turns": exec_turns,
            "git_commit": "abc1234",
            "git_diff_patch": "--- a/ivt.py\n+++ b/ivt.py\n@@ -1 +1 @@\n-200\n+300\n",
        },
    )
    assert handoff.status_code == 200

    # DeepSeek claims handoff, reads package (5 Chat turns + 10 exec turns), confirms
    claimed = client.post(
        f"/api/v1/hub/tasks/{task_id}/handoff/claim",
        headers=DEEPSEEK_HEADERS,
        json={"session_id": "claude-sess-2"},
    )
    assert claimed.status_code == 200
    assert claimed.json()["status"] == "HANDED_OFF"
    assert claimed.json()["assigned_agent"] == "deepseek"

    pkg = client.get(f"/api/v1/hub/tasks/{task_id}/handoff/package", headers=DEEPSEEK_HEADERS).json()
    assert len(pkg["recent_chat_turns"]) == 5
    assert pkg["recent_chat_turns"][0]["content"] == "Chat规划第4轮"
    assert len(pkg["previous_execution_recent_turns"]) == 10
    assert pkg["previous_execution_recent_turns"][0]["content"] == "GPT执行轮次 3"
    assert "+300" in pkg["git_diff_patch"]

    confirmed = client.post(
        f"/api/v1/hub/tasks/{task_id}/handoff/confirm",
        headers=DEEPSEEK_HEADERS,
        json={"session_id": "claude-sess-2", "workspace_verified_note": "git diff verified"},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "RESUMED"
