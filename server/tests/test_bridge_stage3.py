"""Stage 3 tests for Chat Bridge context loader, IN_CHAT state, and triage inbox."""

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


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_file = tmp_path / "test_bridge_stage3.db"
    monkeypatch.setenv("TOWER_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_file}")
    monkeypatch.setenv("TOWER_BRIDGE_TOKEN", "test-bridge-token")
    monkeypatch.setenv("TOWER_AGENT_TOKEN_GEMINI", "test-gemini-token")

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


def test_bridge_context_load_triage_and_in_chat(client: TestClient) -> None:
    assert client.post(
        "/api/v1/bridge/threads/register",
        headers=BRIDGE_HEADERS,
        json={
            "planner_thread_id": "CHAT_AR_MAIN",
            "project_id": "AR_NP",
            "title": "北太平洋大气河",
            "sync_enabled": True,
        },
    ).status_code == 200

    assert client.post(
        "/api/v1/bridge/messages/sync",
        headers=BRIDGE_HEADERS,
        json={
            "planner_thread_id": "CHAT_AR_MAIN",
            "messages": [
                {"role": "user", "content": "ERA5 网格分辨率选 0.25 度", "external_message_id": "m1"},
                {"role": "assistant", "content": "确认采用 0.25 度经纬度网格", "external_message_id": "m2"},
            ],
        },
    ).status_code == 200

    assert client.post(
        "/api/v1/bridge/decisions/publish",
        headers=BRIDGE_HEADERS,
        json={
            "project_id": "AR_NP",
            "planner_thread_id": "CHAT_AR_MAIN",
            "title": "Resolution 0.25 deg",
            "decision_content": "Use 0.25 deg ERA5 grid.",
        },
    ).status_code == 200

    # Agent submits an unassigned question -> NEEDS_TRIAGE
    q_resp = client.post(
        "/api/v1/hub/questions",
        headers=GEMINI_HEADERS,
        json={"question_text": "Should we include DJF 2024?", "block_task": False},
    )
    assert q_resp.status_code == 200
    qid = q_resp.json()["question_id"]
    assert q_resp.json()["status"] == "NEEDS_TRIAGE"

    # Agents must NOT access bridge context/load or triage
    assert client.get("/api/v1/bridge/inbox/triage", headers=GEMINI_HEADERS).status_code == 403
    assert (
        client.post(
            "/api/v1/bridge/context/load",
            headers=GEMINI_HEADERS,
            json={"planner_thread_id": "CHAT_AR_MAIN"},
        ).status_code
        == 403
    )

    triage = client.get("/api/v1/bridge/inbox/triage", headers=BRIDGE_HEADERS).json()
    assert len(triage) == 1 and triage[0]["question_id"] == qid

    in_chat = client.post(
        f"/api/v1/bridge/questions/{qid}/in-chat",
        headers=BRIDGE_HEADERS,
        json={"planner_thread_id": "CHAT_AR_MAIN"},
    )
    assert in_chat.status_code == 200
    assert in_chat.json()["status"] == "IN_CHAT"
    assert in_chat.json()["planner_thread_id"] == "CHAT_AR_MAIN"

    ctx_resp = client.post(
        "/api/v1/bridge/context/load",
        headers=BRIDGE_HEADERS,
        json={"planner_thread_id": "CHAT_AR_MAIN", "search_query": "0.25"},
    )
    assert ctx_resp.status_code == 200
    body = ctx_resp.json()
    assert "Resolution 0.25 deg" in body["formatted_prompt"]
    assert len(body["search_hits"]) == 2
