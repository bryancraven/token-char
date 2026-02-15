# CLAUDE.md

## What This Is

A standalone, stdlib-only Python toolkit that extracts per-turn token usage data from Claude Desktop (Cowork), Claude Code (CLI), and OpenAI Codex session logs. Supports Codex CLI, Codex Desktop app, and Codex VS Code extension sessions. Produces structured JSON/CSV/JSONL/table output at per-turn grain — sufficient to recreate all charts/tables from the PDF reports in the sibling `cowork_usage_report` repo.

## Running

```bash
# Extract all sources to stdout as JSON
python -m token_char.extract

# Human-readable terminal summary with per-source stats
python -m token_char.extract --format table

# Extract Cowork only, CSV output
python -m token_char.extract --source cowork --format csv --output ./out/

# Remote extraction via SSH
ssh user@host python3 < scripts/remote_extract.py > host.json
```

## Dependencies

**Zero runtime dependencies.** Everything uses Python stdlib. Tests need `pytest`.

```bash
pip install pytest  # for testing only
```

## Architecture

```
token_char/
├── schema.py          # Field definitions, validation
├── sources/
│   ├── _common.py     # Shared: timestamp parsing, model_family, platform paths (macOS/Linux/Windows)
│   ├── cowork.py      # Cowork (Claude Desktop) parser
│   ├── claude_code.py # Claude Code (CLI) parser
│   └── codex.py       # OpenAI Codex parser (CLI, Desktop app, and VS Code extension)
├── output.py          # JSON/CSV/JSONL writers
├── stats.py           # Stdlib-only statistics (percentiles, composition, turn profiles)
├── table.py           # Human-readable terminal table renderer (--format table)
└── extract.py         # CLI entry point (also __main__.py)
```

### Data Flow

1. `extract.py` parses CLI args, resolves platform-specific data directories
2. Source parsers (`cowork.py`, `claude_code.py`, `codex.py`) read session files and produce lists of turn/session dicts conforming to `schema.py`
3. `output.py` serializes to JSON/CSV/JSONL; `table.py` renders human-readable terminal output via `stats.py`

### Key Patterns

- **Per-turn grain**: Every assistant response is one turn dict with 4 token fields (input, output, cache_read, cache_create) plus `reasoning_output_tokens` and `is_subagent`/`subagent_id` for provenance
- **reasoning_output_tokens**: Informational field — subset of `output_tokens`, NOT additive in `total_tokens`. Non-zero for Codex (from OpenAI API), 0 for Claude sources
- **Subagent parsing**: Claude Code sessions may have `<session-id>/subagents/agent-<id>.jsonl` files — these are parsed automatically and their tokens included in session aggregates
- **Windows path encoding**: Project directory names use drive letter + `--` + path segments separated by `-` (e.g. `C--Users-foo-bar` → `C:\Users\foo\bar`)
- **Codex token decomposition**: OpenAI convention — input includes cached, output includes reasoning. Parser decomposes: `our_input = codex_input - codex_cached`, `our_cache_read = codex_cached`
- **Codex turn boundaries**: Three protocols in priority order: (1) `task_started`/`task_complete` pairs for newer CLI sessions, (2) non-zero `token_count` deltas for Desktop/VSCode sessions (per-API-call grain), (3) single synthetic turn from session-level totals as last resort
- **Codex originators**: Sessions have an `originator` field in `session_meta`: `"Codex Desktop"` (app), `"codex_vscode"` (VS Code extension), or `"codex_cli_rs"` (CLI). All write to the same `~/.codex/sessions/` directory
- **ASCII fallback**: `table.py` auto-detects stdout encoding; uses Unicode box-drawing on UTF-8 terminals, ASCII equivalents (`=`, `-`, `|`) on cp1252/others. `--ascii` flag forces ASCII mode. Windows stdout is reconfigured to UTF-8 when possible
- **model_family()**: Substring classification → opus/sonnet/haiku/gpt/unknown
- **is_genuine_user_turn()**: Filters out tool_result callback lists from user turn counts
- **Cowork timestamp**: `_audit_timestamp` field (with fallback to `message._audit_timestamp`)
- **Claude Code timestamp**: `timestamp` field directly on the record

### Data Sources

- **Cowork**:
  - macOS: `~/Library/Application Support/Claude/local-agent-mode-sessions/<org>/<project>/`
  - Linux: `~/.config/Claude/local-agent-mode-sessions/<org>/<project>/`
  - Windows: `%APPDATA%\Claude\local-agent-mode-sessions\<org>\<project>\` (path defined but untested)
  - `local_<sid>.json` — metadata (title, model, timestamps as epoch-ms)
  - `local_<sid>/audit.jsonl` — per-message audit log
- **Claude Code**:
  - macOS/Linux: `~/.claude/projects/<encoded-path>/`
  - Windows: `%USERPROFILE%\.claude\projects\<encoded-path>\`
  - `<session-id>.jsonl` — main session log
  - `<session-id>/subagents/agent-<id>.jsonl` — subagent logs (parsed automatically)
- **Codex** (CLI, Desktop app, and VS Code extension):
  - All platforms: `~/.codex/sessions/YYYY/MM/DD/`
  - `rollout-<timestamp>-<session-id>.jsonl` — full session log
  - All three Codex clients (CLI, Desktop app, VS Code) write to the same directory
  - Turn boundary protocol varies by client:
    - CLI (newer): `task_started`/`task_complete` event pairs
    - Desktop app / VS Code / older CLI: no task boundary events; parser uses non-zero `token_count` deltas (each = one API call) for per-turn granularity
  - `session_meta.originator` identifies the client: `"Codex Desktop"`, `"codex_vscode"`, `"codex_cli_rs"`
  - Desktop app data is NOT in `~/Library/Application Support/Codex/` — that's just Electron app state (binary/encrypted). The actual session JSONL logs are in `~/.codex/sessions/`
- **GitHub Copilot** (native VS Code agent): NOT supported — does not log token usage locally

## Testing

```bash
pytest tests/
```

Tests use synthetic fixtures in `tests/fixtures/`. No real session data needed.
