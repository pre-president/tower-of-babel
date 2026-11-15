"""Local Windows CLI for DeepSeek 1M rolling conversation compaction."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

_BRIDGE_ROOT = Path(__file__).resolve().parent.parent
if str(_BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_BRIDGE_ROOT))

from tower_bridge.config import BridgeConfig, HubClient, HubHttpError  # noqa: E402
from tower_bridge.summarizer import SummarizerConfig, SummarizerError, summarize_batch  # noqa: E402


def _make_client(agent: str | None = None) -> HubClient:
    cfg = BridgeConfig.from_env(agent_hint=agent if agent and agent != "auto" else None)
    return HubClient(cfg)


def _route_prefix(agent: str | None) -> str:
    if agent and agent not in ("auto", "agent", "bridge"):
        return "/api/v1/hub"
    return "/api/v1/bridge"


def compact_thread(
    client: HubClient,
    summarizer: SummarizerConfig,
    *,
    planner_thread_id: str,
    prefix: str = "/api/v1/bridge",
    protect_turns: int = 20,
    threshold_chars: int = 150_000,
    max_batch_chars: int = 250_000,
    max_batches: int = 5,
    force: bool = False,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Run up to `max_batches` rolling compaction cycles on a single planner thread."""
    results: list[dict[str, Any]] = []
    for batch_idx in range(1, max_batches + 1):
        plan, _ = client.request_json(
            "GET",
            f"{prefix}/threads/{planner_thread_id}/compaction-plan",
            query={
                "protect_recent_turns": protect_turns,
                "threshold_chars": threshold_chars,
                "max_batch_chars": max_batch_chars,
                "force": str(force).lower(),
            },
        )
        if not isinstance(plan, dict) or not plan.get("should_compact"):
            if batch_idx == 1:
                results.append(
                    {
                        "planner_thread_id": planner_thread_id,
                        "status": "skipped",
                        "eligible_char_count": plan.get("eligible_char_count", 0)
                        if isinstance(plan, dict)
                        else 0,
                        "threshold_chars": threshold_chars,
                    }
                )
            break

        messages = plan.get("messages") or []
        start_turn = plan.get("start_turn")
        end_turn = plan.get("end_turn")
        batch_chars = int(plan.get("batch_char_count") or 0)
        if not messages or start_turn is None or end_turn is None:
            break

        if dry_run:
            results.append(
                {
                    "planner_thread_id": planner_thread_id,
                    "status": "dry_run",
                    "batch": batch_idx,
                    "start_turn": start_turn,
                    "end_turn": end_turn,
                    "batch_char_count": batch_chars,
                    "message_count": len(messages),
                    "model": summarizer.model,
                    "protocol": summarizer.protocol,
                }
            )
            break

        digest_md = summarize_batch(
            summarizer,
            planner_thread_id=planner_thread_id,
            title=str(plan.get("title") or planner_thread_id),
            start_turn=int(start_turn),
            end_turn=int(end_turn),
            messages=messages,
        )
        saved, _ = client.request_json(
            "POST",
            f"{prefix}/threads/{planner_thread_id}/digests",
            body={
                "start_turn": int(start_turn),
                "end_turn": int(end_turn),
                "source_char_count": max(1, batch_chars),
                "digest_content": digest_md,
                "summarizer_model": summarizer.model,
                "protect_recent_turns": protect_turns,
            },
        )
        results.append(
            {
                "planner_thread_id": planner_thread_id,
                "status": "compacted",
                "batch": batch_idx,
                "digest_id": saved.get("digest_id") if isinstance(saved, dict) else None,
                "start_turn": start_turn,
                "end_turn": end_turn,
                "source_char_count": batch_chars,
                "digest_char_count": len(digest_md),
            }
        )
        # Only force the first batch when --force is specified unless subsequent batch still clears threshold
        force = False
    return results


def cmd_doctor(args: argparse.Namespace) -> int:
    client = _make_client(args.agent)
    summarizer = SummarizerConfig.discover()
    health_status: Any = "unknown"
    try:
        health_status, _ = client.request_json("GET", "/health")
    except HubHttpError as exc:
        health_status = f"ERROR: {exc}"

    out = {
        "hub_url": client.config.hub_url,
        "hub_token_configured": bool(client.config.token),
        "hub_health": health_status,
        "summarizer": summarizer.status_dict(),
    }
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        s = out["summarizer"]
        print(f"Hub URL    : {out['hub_url']} (token={'configured' if out['hub_token_configured'] else 'MISSING'})")
        print(f"Hub Health : {out['hub_health']}")
        print(f"Summarizer : protocol={s['protocol']} | model={s['model']} | key={'configured' if s['api_key_configured'] else 'MISSING'}")
        print(f"Endpoint   : {s['endpoint']} (source={s['source']})")
    return 0


def cmd_candidates(args: argparse.Namespace) -> int:
    client = _make_client(None)
    rows, _ = client.request_json(
        "GET",
        "/api/v1/bridge/compaction/candidates",
        query={
            "protect_recent_turns": args.protect_turns,
            "threshold_chars": args.threshold_chars,
        },
    )
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        if not rows:
            print("No sync-enabled threads registered.")
        for c in rows:
            flag = "READY" if c["should_compact"] else "wait "
            print(
                f"[{flag}] {c['planner_thread_id']} | eligible={c['eligible_char_count']:,} chars "
                f"| covered<=turn {c['covered_until_turn']} / total {c['total_turns']} | {c['title']}"
            )
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    if not args.thread and not args.all:
        print("Specify either --thread <planner_thread_id> or --all.", file=sys.stderr)
        return 2

    client = _make_client(args.agent if args.thread else None)
    prefix = _route_prefix(args.agent if args.thread else None)
    summarizer = SummarizerConfig.discover()

    targets: list[str] = []
    if args.thread:
        targets = [args.thread]
    else:
        rows, _ = client.request_json(
            "GET",
            "/api/v1/bridge/compaction/candidates",
            query={
                "protect_recent_turns": args.protect_turns,
                "threshold_chars": args.threshold_chars,
            },
        )
        targets = [
            r["planner_thread_id"]
            for r in (rows or [])
            if args.force or r.get("should_compact")
        ]

    all_results: list[dict[str, Any]] = []
    for tid in targets:
        try:
            res = compact_thread(
                client,
                summarizer,
                planner_thread_id=tid,
                prefix=prefix,
                protect_turns=args.protect_turns,
                threshold_chars=args.threshold_chars,
                max_batch_chars=args.max_batch_chars,
                max_batches=args.max_batches,
                force=args.force,
                dry_run=args.dry_run,
            )
            all_results.extend(res)
        except (HubHttpError, SummarizerError) as exc:
            all_results.append(
                {"planner_thread_id": tid, "status": "error", "error": str(exc)}
            )

    if args.json:
        print(json.dumps(all_results, ensure_ascii=False, indent=2))
    else:
        if not all_results:
            print("No compaction candidates reached threshold.")
        for r in all_results:
            st = r["status"]
            if st == "compacted":
                print(
                    f"[COMPACTED] {r['planner_thread_id']} turns {r['start_turn']}..{r['end_turn']} "
                    f"({r['source_char_count']:,} -> {r['digest_char_count']:,} chars, digest={r['digest_id']})"
                )
            elif st == "dry_run":
                print(
                    f"[DRY-RUN] {r['planner_thread_id']} turns {r['start_turn']}..{r['end_turn']} "
                    f"({r['batch_char_count']:,} chars, {r['message_count']} msgs via {r['model']})"
                )
            elif st == "skipped":
                print(
                    f"[SKIP] {r['planner_thread_id']} eligible={r['eligible_char_count']:,} < threshold={r['threshold_chars']:,}"
                )
            else:
                print(f"[ERROR] {r['planner_thread_id']}: {r.get('error')}")
    has_err = any(r.get("status") == "error" for r in all_results)
    return 1 if has_err else 0


def cmd_watch(args: argparse.Namespace) -> int:
    client = _make_client(None)
    summarizer = SummarizerConfig.discover()
    cycles = 0
    while True:
        cycles += 1
        try:
            rows, _ = client.request_json(
                "GET",
                "/api/v1/bridge/compaction/candidates",
                query={
                    "protect_recent_turns": args.protect_turns,
                    "threshold_chars": args.threshold_chars,
                },
            )
            ready = [r["planner_thread_id"] for r in (rows or []) if r.get("should_compact")]
            for tid in ready:
                res = compact_thread(
                    client,
                    summarizer,
                    planner_thread_id=tid,
                    protect_turns=args.protect_turns,
                    threshold_chars=args.threshold_chars,
                    max_batch_chars=args.max_batch_chars,
                    max_batches=args.max_batches,
                    force=False,
                    dry_run=args.dry_run,
                )
                for item in res:
                    print(json.dumps(item, ensure_ascii=False))
        except (HubHttpError, SummarizerError) as exc:
            print(f"[watch] cycle {cycles} warning: {exc}", file=sys.stderr)

        if args.iterations > 0 and cycles >= args.iterations:
            break
        time.sleep(max(1, args.interval))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tower-compact",
        description="Tower of Babel local DeepSeek 1M rolling conversation compactor",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_doc = sub.add_parser("doctor", help="Inspect Hub + local DeepSeek/Anthropic/OpenAI summarizer config")
    p_doc.add_argument("--agent", default=None, help="Optional agent profile hint")
    p_doc.add_argument("--json", action="store_true")
    p_doc.set_defaults(func=cmd_doctor)

    p_cand = sub.add_parser("candidates", help="List planner threads and compaction readiness")
    p_cand.add_argument("--protect-turns", type=int, default=20)
    p_cand.add_argument("--threshold-chars", type=int, default=150_000)
    p_cand.add_argument("--json", action="store_true")
    p_cand.set_defaults(func=cmd_candidates)

    p_run = sub.add_parser("run", help="Compact one thread or all ready threads")
    p_run.add_argument("--thread", default=None, help="Specific planner_thread_id")
    p_run.add_argument("--all", action="store_true", help="Process all ready candidate threads")
    p_run.add_argument("--agent", default=None, help="Optional actor profile for --thread (e.g. deepseek)")
    p_run.add_argument("--protect-turns", type=int, default=20)
    p_run.add_argument("--threshold-chars", type=int, default=150_000)
    p_run.add_argument("--max-batch-chars", type=int, default=250_000)
    p_run.add_argument("--max-batches", type=int, default=5)
    p_run.add_argument("--force", action="store_true", help="Compact eligible turns even under threshold")
    p_run.add_argument("--dry-run", action="store_true", help="Plan batch without calling summarizer or uploading")
    p_run.add_argument("--json", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_watch = sub.add_parser("watch", help="Poll compaction candidates continuously on local machine")
    p_watch.add_argument("--interval", type=int, default=300, help="Polling interval in seconds")
    p_watch.add_argument("--iterations", type=int, default=0, help="Stop after N cycles (0 = forever)")
    p_watch.add_argument("--protect-turns", type=int, default=20)
    p_watch.add_argument("--threshold-chars", type=int, default=150_000)
    p_watch.add_argument("--max-batch-chars", type=int, default=250_000)
    p_watch.add_argument("--max-batches", type=int, default=5)
    p_watch.add_argument("--dry-run", action="store_true")
    p_watch.set_defaults(func=cmd_watch)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
