# Tower of Babel — Local Client Bridge (`client-bridge`)

Zero-dependency (Python 3.10+ stdlib only) local bridge toolkit connecting local coding agents (**Antigravity / Gemini**, **Claude Code / DeepSeek**, **Codex / GPT**, or community adapters) to your central Hub (`https://hub.atmosphericduct.xyz`).

## Modules

- `tower_bridge/adapters/`: Pluggable `TranscriptAdapter` registry (`ClaudeCodeAdapter`, `CodexAdapter`, `AntigravityAdapter`) extracting clean visible conversation turns (`~10` latest turns) from massive `.jsonl` logs while stripping tool-call/thinking JSON noise.
- `tower_bridge/git_snapshot.py`: Captures `git branch`, `git commit`, status, and `git diff HEAD` patch (with automatic 512 KB inline cap + local patch pointer).
- `tower_bridge/mcp_stdio_proxy.py`: Standard stdio MCP server forwarding remote `/mcp` Hub tools over HTTPS Bearer auth, plus optional local inspection/handoff tools.
- `tower_bridge/handoff_cli.py` (`tower-handoff`): CLI for manual inspection, starting work from a Chat name, instant/quota-exhausted handoffs, and handoff pickup.
- `tower_bridge/summarizer.py`, `tower_bridge/compact_cli.py` (`tower-compact`), & `tower_bridge/power.py`: Local rolling-compaction runner and power-aware Windows/Linux scheduler keeping DeepSeek/Anthropic/OpenAI API keys strictly on the local machine (auto-discovers `~/.claude/settings.json` or `~/.tower-of-babel/summarizer.env` with zero key logging), preserving the latest 20 turns untouched and compressing older `150K–250K` char spans into `4K–6K` structured digests (`10 min` cadence on AC power, `30 min` on battery).
- `install_autocompact.ps1`: Registers a silent Windows Task Scheduler job (`TowerOfBabelAutoCompact` via `pythonw.exe`) running `compact_cli.py tick --ac-minutes 10 --battery-minutes 30` cleanly at logon and on a 10-minute timer.
- `register_clients.py`: Backs up and configures Antigravity (`~/.gemini/config/mcp_config.json`), Claude Code (`~/.claude.json`), and Codex (`~/.codex/config.toml`) cleanly without writing secret tokens into shared config files.

## Quick CLI Usage

```bash
python -m tower_bridge.handoff_cli doctor --repo .
python -m tower_bridge.handoff_cli inspect --client auto --limit 10
python -m tower_bridge.compact_cli doctor
python -m tower_bridge.compact_cli candidates
python -m tower_bridge.compact_cli run --all --dry-run
python -m tower_bridge.compact_cli tick --ac-minutes 10 --battery-minutes 30 --json
```

Windows silent auto-compaction task management:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_autocompact.ps1 -AcMinutes 10 -BatteryMinutes 30
powershell -ExecutionPolicy Bypass -File .\install_autocompact.ps1 -Status
powershell -ExecutionPolicy Bypass -File .\install_autocompact.ps1 -Uninstall
```

## Adding a Community Adapter

Implement `TranscriptAdapter` in `tower_bridge/adapters/` (define `client_name`, `discover_sessions`, `parse_turns`) and register it in `default_registry()`.