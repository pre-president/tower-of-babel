"""Additional edge-case tests for Stage 1 Hub invariants and boundaries."""

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


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_file = tmp_path / "edge_hub.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_file}")
    monkeypatch.setenv("TOWER_BRIDGE_TOKEN", "test-bridge-token")
    monkeypatch.setenv("TOWER_AGENT_TOKEN_GEMINI", "test-gemini-token")
    monkeypatch.setenv("TOWER_AGENT_TOKEN_DEEPSEEK", "test-deepseek-token")
    monkeypatch.setenv("TOWER_AGENT_TOKEN_GPT", "test-gpt-token")

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    Base.metadata.create_all(bind=get_engine())

    with TestClient(create_app()) as tc:
        yield tc

    get_engine().dispose()
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()


def test_disabled_sync_and_ambiguous_chat_ref(client: TestClient) -> None:
    client.post(
        "/api/v1/bridge/threads/register",
        headers=BRIDGE_HEADERS,
        json={
            "planner_thread_id": "CHAT_AR_1",
            "project_id": "AR",
            "title": "大气河研究主窗口",
            "sync_enabled": False,
        },
    )
    client.post(
        "/api/v1/bridge/threads/register",
        headers=BRIDGE_HEADERS,
        json={
            "planner_thread_id": "CHAT_AR_2",
            "project_id": "AR",
            "title": "大气河研究论文窗口",
            "sync_enabled": True,
        },
    )

    # Disabled sync thread must reject ingestion
    r = client.post(
        "/api/v1/bridge/messages/sync",
        headers=BRIDGE_HEADERS,
        json={
            "planner_thread_id": "CHAT_AR_1",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert r.status_code == 403

    # Ambiguous partial title returns 409 rather than silently picking wrong window
    amb = client.get(
        "/api/v1/hub/threads/resolve",
        headers=GEMINI_HEADERS,
        params={"chat_ref": "大气河研究"},
    )
    assert amb.status_code == 409


def test_triage_question_invalid_transition_and_artifacts(client: TestClient) -> None:
    client.post(
        "/api/v1/bridge/threads/register",
        headers=BRIDGE_HEADERS,
        json={
            "planner_thread_id": "CHAT_PDSR",
            "project_id": "PDSR",
            "title": "PDSR",
            "sync_enabled": True,
        },
    )

    # Question without thread or task enters NEEDS_TRIAGE
    q = client.post(
        "/api/v1/hub/questions",
        headers=DEEPSEEK_HEADERS,
        json={"question_text": "这个指标归属哪个窗口确认？"},
    )
    assert q.status_code == 200
    assert q.json()["status"] == "NEEDS_TRIAGE"

    # Register local file pointer artifact
    art = client.post(
        "/api/v1/hub/artifacts",
        headers=GEMINI_HEADERS,
        json={
            "project_id": "PDSR",
            "kind": "local_pointer",
            "path_or_uri": "C:/Projects/PDSR/train.py",
            "sha256": "deadbeef" * 8,
            "pinned": True,
        },
    )
    assert art.status_code == 200
    assert art.json()["pinned"] is True

    # Invalid task status jump PLANNED -> COMPLETED must be rejected
    task = client.post(
        "/api/v1/hub/tasks",
        headers=BRIDGE_HEADERS,
        json={
            "project_id": "PDSR",
            "planner_thread_id": "CHAT_PDSR",
            "title": "测试状态机",
            "goal_description": "校验非法跳变",
        },
    ).json()
    bad = client.post(
        f"/api/v1/hub/tasks/{task['task_id']}/status",
        headers=GEMINI_HEADERS,
        json={"status": "COMPLETED"},
    )
    assert bad.status_code == 409
