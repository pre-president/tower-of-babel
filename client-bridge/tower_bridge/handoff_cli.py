"""CLI tool for inspecting local .jsonl logs and running Tower of Babel handoffs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_BRIDGE_ROOT = Path(__file__).resolve().parent.parent
if str(_BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_BRIDGE_ROOT))

from tower_bridge.adapters import default_registry  # noqa: E402
from tower_bridge.config import BridgeConfig, HubClient, HubHttpError  # noqa: E402
from tower_bridge.git_snapshot import capture_git_snapshot  # noqa: E402


def _make_client(agent: str | None = None) -> HubClient:
    cfg = BridgeConfig.from_env(agent_hint=agent if agent and agent != "auto" else None)
    return HubClient(cfg)


def cmd_doctor(args: argparse.Namespace) -> int:
    reg = default_registry()
    client = _make_client(args.agent)
    snap = capture_git_snapshot(args.repo, write_oversize_file=False)
    print(f"Hub URL : {client.config.hub_url}")
    print(f"Token   : {'configured' if client.config.token else 'MISSING'}")
    try:
        health, _ = client.request_json("GET", "/health")
        print(f"Health  : {health}")
    except HubHttpError as exc:
        print(f"Health  : ERROR ({exc})")

    print("Adapters:", ", ".join(reg.registered_clients()))
    for name in reg.registered_clients():
        res = reg.extract_recent(client=name, repo_path=args.repo, limit=1)
        sess = res.get("session")
        print(f"  - {name}: {sess['file_path'] if sess else 'no sessions found'}")
    print(f"Git repo: {snap.is_git_repo} (branch={snap.git_branch}, commit={snap.git_commit})")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    reg = default_registry()
    extracted = reg.extract_recent(
        client=args.client,
        repo_path=args.repo,
        session_file=args.session_file,
        limit=args.limit,
    )
    snap = capture_git_snapshot(args.repo, write_oversize_file=False)
    out = {"session": extracted.get("session"), "recent_turns": extracted.get("recent_turns", []), "git": snap.to_dict()}
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        sess = out["session"] or {}
        print(f"Client  : {sess.get('client')} | File: {sess.get('file_path')}")
        print(f"Git     : {snap.git_branch} @ {snap.git_commit}")
        print(f"Turns   : {len(out['recent_turns'])}")
        for i, t in enumerate(out["recent_turns"], 1):
            preview = t["content"].replace("\n", " ")[:120]
            print(f"  [{i:02d}] {t['role']}: {preview}")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    client = _make_client(args.agent)
    snap = capture_git_snapshot(args.repo, write_oversize_file=False)
    payload: dict[str, Any] = {
        "chat_ref": args.chat,
        "task_title": args.title,
        "goal_description": args.goal,
        "session_id": args.session_id,
        "local_repo_path": str(Path(args.repo).resolve()),
        "git_branch": snap.git_branch,
        "chat_turn_limit": args.chat_turns,
    }
    res, _ = client.request_json("POST", "/api/v1/hub/work/start", body=payload)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_handoff(args: argparse.Namespace) -> int:
    client = _make_client(args.agent)
    reg = default_registry()
    extracted = reg.extract_recent(
        client=args.client,
        repo_path=args.repo,
        session_file=args.session_file,
        limit=args.limit,
    )
    snap = capture_git_snapshot(args.repo, task_id=args.task_id, write_oversize_file=True)
    turns = extracted.get("recent_turns", [])
    digest = args.digest.strip()
    if not digest:
        digest = (
            f"Captured from {(extracted.get('session') or {}).get('client', args.client)} "
            f"on branch {snap.git_branch or 'N/A'}"
        )
    artifact_refs = [snap.oversized_patch_ref] if snap.oversized_patch_ref else []
    payload = {
        "session_id": args.session_id,
        "reason": "quota_exhausted" if args.quota_exhausted else "manual_handoff",
        "execution_digest": digest,
        "completed_steps": args.completed or [],
        "current_step": args.current_step or "",
        "next_steps": args.next_step or [],
        "blockers": args.blocker or ("Model quota exhausted" if args.quota_exhausted else ""),
        "recent_turns": [
            {"role": t["role"], "content": t["content"], "timestamp": t.get("timestamp")}
            for t in turns[-10:]
        ],
        "execution_trace_excerpt": extracted.get("excerpt", "")[:24000],
        "git_commit": snap.git_commit,
        "git_diff_patch": snap.git_diff_patch,
        "local_artifact_refs": artifact_refs,
    }
    res, _ = client.request_json("POST", f"/api/v1/hub/tasks/{args.task_id}/handoff", body=payload)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_pickup(args: argparse.Namespace) -> int:
    client = _make_client(args.agent)
    claimed, _ = client.request_json(
        "POST",
        f"/api/v1/hub/tasks/{args.task_id}/handoff/claim",
        body={"session_id": args.session_id, "lease_minutes": args.lease_minutes},
    )
    pkg, _ = client.request_json("GET", f"/api/v1/hub/tasks/{args.task_id}/handoff/package")
    if args.confirm:
        client.request_json(
            "POST",
            f"/api/v1/hub/tasks/{args.task_id}/handoff/confirm",
            body={
                "session_id": args.session_id,
                "workspace_verified_note": args.verify_note or "Verified via tower-handoff pickup",
            },
        )
    if args.json:
        print(json.dumps({"task": claimed, "package": pkg}, ensure_ascii=False, indent=2))
    else:
        print(f"=== Handoff Pickup: {pkg['task']['task_id']} ({pkg['task']['title']}) ===")
        print(f"Active Decisions : {len(pkg['active_decisions'])}")
        print(f"ChatGPT Recent   : {len(pkg['recent_chat_turns'])} turns")
        print(f"Prev Exec Digest : {pkg['previous_execution_digest']}")
        print(f"Prev Exec Turns  : {len(pkg['previous_execution_recent_turns'])} turns")
        print(f"Next Steps       : {pkg['next_steps']}")
        print(f"Git Commit       : {pkg.get('git_commit')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tower-handoff", description="Tower of Babel local handoff CLI")
    p.add_argument("--agent", default="agent", help="Credential hint (gemini|deepseek|gpt|agent)")
    sub = p.add_subparsers(dest="command", required=True)

    doc = sub.add_parser("doctor", help="Check hub connectivity and local .jsonl adapters")
    doc.add_argument("--repo", default=".")
    doc.set_defaults(func=cmd_doctor)

    insp = sub.add_parser("inspect", help="Preview latest ~10 visible turns and git snapshot")
    insp.add_argument("--client", default="auto", help="auto|claude_code|codex|antigravity")
    insp.add_argument("--repo", default=".")
    insp.add_argument("--session-file", default=None)
    insp.add_argument("--limit", type=int, default=10)
    insp.add_argument("--json", action="store_true")
    insp.set_defaults(func=cmd_inspect)

    st = sub.add_parser("start", help="Start a task directly from a Chat name or title")
    st.add_argument("--chat", required=True, help="Planner thread ID, UUID, or title")
    st.add_argument("--title", required=True)
    st.add_argument("--goal", required=True)
    st.add_argument("--session-id", required=True)
    st.add_argument("--repo", default=".")
    st.add_argument("--chat-turns", type=int, default=20)
    st.set_defaults(func=cmd_start)

    ho = sub.add_parser("handoff", help="Package local .jsonl ~10 turns + git diff and hand off")
    ho.add_argument("--task-id", required=True)
    ho.add_argument("--session-id", required=True)
    ho.add_argument("--client", default="auto")
    ho.add_argument("--repo", default=".")
    ho.add_argument("--session-file", default=None)
    ho.add_argument("--limit", type=int, default=10)
    ho.add_argument("--digest", default="")
    ho.add_argument("--current-step", default="")
    ho.add_argument("--blocker", default="")
    ho.add_argument("--completed", action="append")
    ho.add_argument("--next-step", action="append")
    ho.add_argument("--quota-exhausted", action="store_true")
    ho.set_defaults(func=cmd_handoff)

    pk = sub.add_parser("pickup", help="Claim handoff and print concise context package")
    pk.add_argument("--task-id", required=True)
    pk.add_argument("--session-id", required=True)
    pk.add_argument("--lease-minutes", type=int, default=30)
    pk.add_argument("--confirm", action="store_true")
    pk.add_argument("--verify-note", default="")
    pk.add_argument("--json", action="store_true")
    pk.set_defaults(func=cmd_pickup)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())