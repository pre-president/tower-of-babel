# Tower of Babel — Local Client Bridge (`client-bridge`)

Zero-dependency (Python 3.10+ stdlib only) local bridge toolkit connecting local coding agents (**Antigravity / Gemini**, **Claude Code / DeepSeek**, **Codex / GPT**, or community adapters) to your central Hub (`https://hub.atmosphericduct.xyz`).

## Modules

- `tower_bridge/adapters/`: Pluggable `TranscriptAdapter` registry (`ClaudeCodeAdapter`, `CodexAdapter`, `AntigravityAdapter`) extracting clean visible conversation turns (`~10` latest turns) from massive `.jsonl` logs while stripping tool-call/thinking JSON noise.
- `tower_bridge/git_snapshot.py`: Captures `git branch`, `git commit`, status, and `git diff HEAD` patch (with automatic 512 KB inline cap + local patch pointer).
- `tower_bridge/mcp_stdio_proxy.py`: Standard stdio MCP server forwarding remote `/mcp` Hub tools over HTTPS Bearer auth, plus optional local inspection/handoff tools.
- `tower_bridge/handoff_cli.py`: CLI for manual inspection, starting work from a Chat name, instant/quota-exhausted handoffs, and handoff pickup.
- `register_clients.py`: Backs up and configures Antigravity (`~/.gemini/config/mcp_config.json`), Claude Code (`~/.claude.json`), and Codex (`~/.codex/config.toml`) cleanly without writing secret tokens into shared config files.

## Adding a Community Adapter

Implement `TranscriptAdapter` in `tower_bridge/adapters/` (define `client_name`, `discover_sessions`, `parse_turns`) and register it in `default_registry()`.