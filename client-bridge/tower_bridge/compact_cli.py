"""Local Windows CLI for DeepSeek 1M rolling conversation compaction."""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

_BRIDGE_ROOT = Path(__file__).resolve().parent.parent
if str(_BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_BRIDGE_ROOT))

from tower_bridge.config import BridgeConfig, HubClient, HubHttpError  # noqa: E402
from tower_bridge.power import detect_power_source, interval_for_power_source  # noqa: E402
from tower_bridge.summarizer import SummarizerConfig, SummarizerError, summarize_batch  # noqa: E402


def _default_tower_dir() -> Path:
    return Path.home() / ".tower-of-babel"


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

    state_file = _default_tower_dir() / "compact_state.json"
    out = {
        "hub_url": client.config.hub_url,
        "hub_token_configured": bool(client.config.token),
        "hub_health": health_status,
        "summarizer": summarizer.status_dict(),
        "power_source": detect_power_source(),
        "scheduler_state": _load_state(state_file),
    }
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        s = out["summarizer"]
        print(f"Hub URL    : {out['hub_url']} (token={'configured' if out['hub_token_configured'] else 'MISSING'})")
        print(f"Hub Health : {out['hub_health']}")
        print(f"Summarizer : protocol={s['protocol']} | model={s['model']} | key={'configured' if s['api_key_configured'] else 'MISSING'}")
        print(f"Endpoint   : {s['endpoint']} (source={s['source']})")
        print(f"Power Mode : {out['power_source']} (AC=10m / Battery=30m)")
        if out["scheduler_state"]:
            st = out["scheduler_state"]
            print(f"Last Tick  : {st.get('last_check_iso', '-')} | {st.get('last_result_summary', '-')}")
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


def _compact_all_ready(
    client: HubClient,
    summarizer: SummarizerConfig,
    *,
    protect_turns: int = 20,
    threshold_chars: int = 150_000,
    max_batch_chars: int = 250_000,
    max_batches: int = 5,
    force: bool = False,
    dry_run: bool = False,
) -> tuple[int, list[dict[str, Any]]]:
    rows, _ = client.request_json(
        "GET",
        "/api/v1/bridge/compaction/candidates",
        query={
            "protect_recent_turns": protect_turns,
            "threshold_chars": threshold_chars,
        },
    )
    candidates = rows or []
    targets = [
        r["planner_thread_id"]
        for r in candidates
        if force or r.get("should_compact")
    ]
    results: list[dict[str, Any]] = []
    for tid in targets:
        try:
            res = compact_thread(
                client,
                summarizer,
                planner_thread_id=tid,
                protect_turns=protect_turns,
                threshold_chars=threshold_chars,
                max_batch_chars=max_batch_chars,
                max_batches=max_batches,
                force=force,
                dry_run=dry_run,
            )
            results.extend(res)
        except (HubHttpError, SummarizerError) as exc:
            results.append({"planner_thread_id": tid, "status": "error", "error": str(exc)})
    return len(candidates), results


def _load_state(state_file: Path) -> dict[str, Any]:
    if not state_file.is_file():
        return {}
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state_file: Path, data: dict[str, Any]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(state_file)


def _acquire_lock(lock_file: Path, stale_seconds: int = 900) -> bool:
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    if lock_file.exists():
        try:
            age = now - lock_file.stat().st_mtime
            if age > stale_seconds:
                lock_file.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"pid={os.getpid()} ts={int(now)}\n")
        return True
    except FileExistsError:
        return False


def _append_log(log_file: Path, line: str) -> None:
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        if log_file.is_file() and log_file.stat().st_size > 256_000:
            lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
            log_file.write_text("\n".join(lines[-300:]) + "\n", encoding="utf-8")
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {line}\n")
    except OSError:
        pass


def cmd_watch(args: argparse.Namespace) -> int:
    client = _make_client(None)
    summarizer = SummarizerConfig.discover()
    cycles = 0
    while True:
        cycles += 1
        power_src = detect_power_source()
        try:
            _, res = _compact_all_ready(
                client,
                summarizer,
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
        sleep_s = (
            max(1, args.interval)
            if args.interval and args.interval > 0
            else interval_for_power_source(
                power_src,
                ac_minutes=args.ac_minutes,
                battery_minutes=args.battery_minutes,
            )
        )
        time.sleep(sleep_s)
    return 0


def cmd_tick(args: argparse.Namespace) -> int:
    base_dir = _default_tower_dir()
    state_file = Path(args.state_file) if args.state_file else base_dir / "compact_state.json"
    lock_file = Path(args.lock_file) if args.lock_file else base_dir / "compact.lock"
    log_file = Path(args.log_file) if args.log_file else base_dir / "compact.log"

    power_src = args.power_override or detect_power_source()
    target_interval = interval_for_power_source(
        power_src, ac_minutes=args.ac_minutes, battery_minutes=args.battery_minutes
    )
    # 45s tolerance so a 10-min Windows Scheduled Task cadence hits every 10m on AC & 30m on battery
    min_wait = max(0, target_interval - 45)
    now = time.time()
    prev = _load_state(state_file)
    last_check_ts = float(prev.get("last_check_ts") or 0.0)

    if not args.force_check and last_check_ts > 0 and (now - last_check_ts) < min_wait:
        remaining = int(min_wait - (now - last_check_ts))
        out = {
            "status": f"throttled_{power_src}",
            "power_source": power_src,
            "target_interval_seconds": target_interval,
            "next_check_in_seconds": remaining,
        }
        if args.json:
            print(json.dumps(out, ensure_ascii=False))
        return 0

    if not _acquire_lock(lock_file):
        out = {"status": "locked", "power_source": power_src}
        if args.json:
            print(json.dumps(out, ensure_ascii=False))
        return 0

    try:
        client = _make_client(None)
        summarizer = SummarizerConfig.discover()
        cand_count, results = _compact_all_ready(
            client,
            summarizer,
            protect_turns=args.protect_turns,
            threshold_chars=args.threshold_chars,
            max_batch_chars=args.max_batch_chars,
            max_batches=args.max_batches,
            force=False,
            dry_run=args.dry_run,
        )
        compacted_cnt = sum(1 for r in results if r.get("status") == "compacted")
        err_cnt = sum(1 for r in results if r.get("status") == "error")
        summary = f"power={power_src} candidates={cand_count} compacted={compacted_cnt} errors={err_cnt}"
        _append_log(log_file, summary)
        new_state = {
            **prev,
            "last_power_source": power_src,
            "last_check_ts": now,
            "last_check_iso": datetime.datetime.now().isoformat(timespec="seconds"),
            "ac_minutes": args.ac_minutes,
            "battery_minutes": args.battery_minutes,
            "last_candidates_count": cand_count,
            "last_result_summary": summary,
            "last_results": results,
        }
        if compacted_cnt > 0:
            new_state["last_compact_ts"] = now
            new_state["last_compact_iso"] = new_state["last_check_iso"]
        _save_state(state_file, new_state)
        out = {"status": "checked", "power_source": power_src, "candidates": cand_count, "results": results}
        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            print(summary)
        return 1 if err_cnt > 0 else 0
    except (HubHttpError, SummarizerError) as exc:
        err_msg = f"power={power_src} error={exc}"
        _append_log(log_file, err_msg)
        _save_state(
            state_file,
            {
                **prev,
                "last_power_source": power_src,
                "last_check_ts": now,
                "last_check_iso": datetime.datetime.now().isoformat(timespec="seconds"),
                "last_result_summary": err_msg,
            },
        )
        if args.json:
            print(json.dumps({"status": "error", "power_source": power_src, "error": str(exc)}, ensure_ascii=False))
        else:
            print(err_msg, file=sys.stderr)
        return 1
    finally:
        lock_file.unlink(missing_ok=True)


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

    p_watch = sub.add_parser("watch", help="Poll compaction candidates continuously (power-aware by default)")
    p_watch.add_argument("--interval", type=int, default=0, help="Fixed interval in seconds (0 = auto AC/battery)")
    p_watch.add_argument("--ac-minutes", type=int, default=10, help="Polling minutes when on AC power")
    p_watch.add_argument("--battery-minutes", type=int, default=30, help="Polling minutes when on battery")
    p_watch.add_argument("--iterations", type=int, default=0, help="Stop after N cycles (0 = forever)")
    p_watch.add_argument("--protect-turns", type=int, default=20)
    p_watch.add_argument("--threshold-chars", type=int, default=150_000)
    p_watch.add_argument("--max-batch-chars", type=int, default=250_000)
    p_watch.add_argument("--max-batches", type=int, default=5)
    p_watch.add_argument("--dry-run", action="store_true")
    p_watch.set_defaults(func=cmd_watch)

    p_tick = sub.add_parser("tick", help="Power-aware single check/compaction tick for OS schedulers")
    p_tick.add_argument("--ac-minutes", type=int, default=10, help="Check interval when plugged in (minutes)")
    p_tick.add_argument("--battery-minutes", type=int, default=30, help="Check interval on battery (minutes)")
    p_tick.add_argument("--protect-turns", type=int, default=20)
    p_tick.add_argument("--threshold-chars", type=int, default=150_000)
    p_tick.add_argument("--max-batch-chars", type=int, default=250_000)
    p_tick.add_argument("--max-batches", type=int, default=5)
    p_tick.add_argument("--force-check", action="store_true", help="Bypass interval throttle for this tick")
    p_tick.add_argument("--power-override", choices=["ac", "battery"], default=None)
    p_tick.add_argument("--state-file", default=None)
    p_tick.add_argument("--lock-file", default=None)
    p_tick.add_argument("--log-file", default=None)
    p_tick.add_argument("--dry-run", action="store_true")
    p_tick.add_argument("--json", action="store_true")
    p_tick.set_defaults(func=cmd_tick)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
