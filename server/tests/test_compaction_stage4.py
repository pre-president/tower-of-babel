"""Stage 4 tests for DeepSeek 1M rolling compaction plans, digests, RBAC, and MCP tools."""

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
    db_file = tmp_path / "test_compaction_stage4.db"
    monkeypatch.setenv("TOWER_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_file}")
    monkeypatch.setenv("TOWER_BRIDGE_TOKEN", "test-bridge-token")
    monkeypatch.setenv("TOWER_AGENT_TOKEN_GEMINI", "test-gemini-token")
    monkeypatch.setenv("TOWER_AGENT_TOKEN_DEEPSEEK", "test-deepseek-token")
    monkeypatch.setenv("TOWER_SUMMARIZER_ACTORS", "deepseek")

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


def _seed_thread(client: TestClient, thread_id: str, turns: int, chars_per_turn: int) -> None:
    assert (
        client.post(
            "/api/v1/bridge/threads/register",
            headers=BRIDGE_HEADERS,
            json={
                "planner_thread_id": thread_id,
                "project_id": "AR_COMPACT",
                "title": "Rolling Compaction Research",
                "sync_enabled": True,
            },
        ).status_code
        == 200
    )
    msgs = []
    for i in range(1, turns + 1):
        role = "user" if i % 2 == 1 else "assistant"
        prefix = f"[turn-{i:02d}] "
        body = prefix + ("X" * max(1, chars_per_turn - len(prefix)))
        msgs.append({"role": role, "content": body, "external_message_id": f"{thread_id}-{i}"})
    res = client.post(
        "/api/v1/bridge/messages/sync",
        headers=BRIDGE_HEADERS,
        json={"planner_thread_id": thread_id, "messages": msgs},
    )
    assert res.status_code == 200
    assert res.json()["inserted_count"] == turns


def test_compaction_protection_threshold_slicing_and_rbac(client: TestClient) -> None:
    # Seed 30 turns x 16,000 chars each = 480,000 total chars.
    # Latest 20 turns (11..30) = 320,000 chars protected; eligible turns (1..10) = 160,000 chars (> 150K).
    _seed_thread(client, "CHAT_COMPACT_MAIN", turns=30, chars_per_turn=16_000)

    # Also seed a small thread under the 150K threshold (25 turns x 1,000 chars)
    _seed_thread(client, "CHAT_COMPACT_SMALL", turns=25, chars_per_turn=1_000)

    cand_res = client.get(
        "/api/v1/bridge/compaction/candidates",
        headers=BRIDGE_HEADERS,
    )
    assert cand_res.status_code == 200
    cands = {c["planner_thread_id"]: c for c in cand_res.json()}
    assert cands["CHAT_COMPACT_MAIN"]["should_compact"] is True
    assert cands["CHAT_COMPACT_MAIN"]["eligible_char_count"] == 160_000
    assert cands["CHAT_COMPACT_MAIN"]["protected_recent_turns"] == 20
    assert cands["CHAT_COMPACT_SMALL"]["should_compact"] is False
    assert cands["CHAT_COMPACT_SMALL"]["eligible_char_count"] == 5_000

    # Small thread without force -> should_compact=False and empty messages
    small_plan = client.get(
        "/api/v1/bridge/threads/CHAT_COMPACT_SMALL/compaction-plan",
        headers=BRIDGE_HEADERS,
    )
    assert small_plan.status_code == 200
    assert small_plan.json()["should_compact"] is False
    assert small_plan.json()["messages"] == []

    # Small thread with force=True -> plans turns 1..5 cleanly while protecting 6..25
    small_force = client.get(
        "/api/v1/bridge/threads/CHAT_COMPACT_SMALL/compaction-plan?force=true",
        headers=BRIDGE_HEADERS,
    )
    assert small_force.status_code == 200
    assert small_force.json()["should_compact"] is True
    assert small_force.json()["start_turn"] == 1
    assert small_force.json()["end_turn"] == 5
    assert len(small_force.json()["messages"]) == 5

    # Main thread sliced by max_batch_chars=85,000 -> should batch turns 1..5 (80,000 chars)
    plan1 = client.get(
        "/api/v1/bridge/threads/CHAT_COMPACT_MAIN/compaction-plan?max_batch_chars=85000",
        headers=BRIDGE_HEADERS,
    )
    assert plan1.status_code == 200
    p1 = plan1.json()
    assert p1["should_compact"] is True
    assert p1["start_turn"] == 1
    assert p1["end_turn"] == 5
    assert p1["batch_char_count"] == 80_000

    # Gemini is NOT allowed to write digests (403)
    forbidden = client.post(
        "/api/v1/hub/threads/CHAT_COMPACT_MAIN/digests",
        headers=GEMINI_HEADERS,
        json={
            "start_turn": 1,
            "end_turn": 5,
            "source_char_count": 80_000,
            "digest_content": "Unauthorized digest attempt",
            "summarizer_model": "gemini-3.1-pro",
        },
    )
    assert forbidden.status_code == 403

    # DeepSeek summarizer IS allowed via /api/v1/hub/threads/.../digests
    d1 = client.post(
        "/api/v1/hub/threads/CHAT_COMPACT_MAIN/digests",
        headers=DEEPSEEK_HEADERS,
        json={
            "start_turn": 1,
            "end_turn": 5,
            "source_char_count": 80_000,
            "digest_content": "## Stage 1 Digest (turns 1-5)\n- Core parameters locked.",
            "summarizer_model": "deepseek-v4.1-flash[1M]",
        },
    )
    assert d1.status_code == 200
    d1_body = d1.json()
    assert d1_body["start_turn"] == 1
    assert d1_body["end_turn"] == 5

    # Idempotent replay of exact [1..5] returns identical digest_id
    d1_replay = client.post(
        "/api/v1/bridge/threads/CHAT_COMPACT_MAIN/digests",
        headers=BRIDGE_HEADERS,
        json={
            "start_turn": 1,
            "end_turn": 5,
            "source_char_count": 80_000,
            "digest_content": "Different text ignored on idempotent replay",
            "summarizer_model": "deepseek-v4.1-flash[1M]",
        },
    )
    assert d1_replay.status_code == 200
    assert d1_replay.json()["digest_id"] == d1_body["digest_id"]

    # Overlapping turn span [2..6] -> 409 Conflict
    overlap = client.post(
        "/api/v1/hub/threads/CHAT_COMPACT_MAIN/digests",
        headers=DEEPSEEK_HEADERS,
        json={
            "start_turn": 2,
            "end_turn": 6,
            "source_char_count": 80_000,
            "digest_content": "Overlap digest body with at least twenty chars",
            "summarizer_model": "deepseek-v4.1-flash[1M]",
        },
    )
    assert overlap.status_code == 409

    # Touching protected tail (turns 11..30) -> 409 Conflict
    protected = client.post(
        "/api/v1/hub/threads/CHAT_COMPACT_MAIN/digests",
        headers=DEEPSEEK_HEADERS,
        json={
            "start_turn": 6,
            "end_turn": 15,
            "source_char_count": 160_000,
            "digest_content": "Touches protected turns digest body >= 20 chars",
            "summarizer_model": "deepseek-v4.1-flash[1M]",
        },
    )
    assert protected.status_code == 409

    # Force next compaction batch cleanly covers turns 6..10 (leaving 11..30 = 320,000 chars)
    plan2 = client.get(
        "/api/v1/hub/threads/CHAT_COMPACT_MAIN/compaction-plan?force=true",
        headers=DEEPSEEK_HEADERS,
    )
    assert plan2.status_code == 200
    p2 = plan2.json()
    assert p2["should_compact"] is True
    assert p2["start_turn"] == 6
    assert p2["end_turn"] == 10
    assert p2["covered_until_turn"] == 5

    d2 = client.post(
        "/api/v1/hub/threads/CHAT_COMPACT_MAIN/digests",
        headers=DEEPSEEK_HEADERS,
        json={
            "start_turn": 6,
            "end_turn": 10,
            "source_char_count": 80_000,
            "digest_content": "## Stage 2 Digest (turns 6-10)\n- Boundary conditions set.",
            "summarizer_model": "deepseek-v4.1-flash[1M]",
        },
    )
    assert d2.status_code == 200

    # Exact uncompressed_char_count recomputed = 20 protected turns * 16,000 = 320,000
    threads = client.get("/api/v1/hub/threads", headers=GEMINI_HEADERS).json()
    main_t = next(t for t in threads if t["planner_thread_id"] == "CHAT_COMPACT_MAIN")
    assert main_t["uncompressed_char_count"] == 320_000

    # Zero deleted original DialogueMessage rows
    all_msgs = client.get(
        "/api/v1/hub/dialogues/CHAT_COMPACT_MAIN/range?start_turn=1&end_turn=30",
        headers=GEMINI_HEADERS,
    )
    assert all_msgs.status_code == 200
    assert len(all_msgs.json()) == 30

    # Hub REST endpoint /threads/{id}/digests lists both digests chronologically
    rest_digests = client.get(
        "/api/v1/hub/threads/CHAT_COMPACT_MAIN/digests",
        headers=GEMINI_HEADERS,
    )
    assert rest_digests.status_code == 200
    assert len(rest_digests.json()) == 2

    # MCP get_thread_digests returns both digests with _security_notice boundary
    mcp_res = client.post(
        "/mcp",
        headers=GEMINI_HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 42,
            "method": "tools/call",
            "params": {
                "name": "get_thread_digests",
                "arguments": {"planner_thread_id": "CHAT_COMPACT_MAIN"},
            },
        },
    )
    assert mcp_res.status_code == 200
    sc = mcp_res.json()["result"]["structuredContent"]
    assert sc["count"] == 2
    assert "_security_notice" in sc
    assert sc["items"][0]["start_turn"] == 1
    assert sc["items"][1]["start_turn"] == 6
