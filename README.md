# token-char

Extract per-turn token usage data from Claude Desktop (Cowork), Claude Code (CLI), and OpenAI Codex session logs.

Zero runtime dependencies. Python 3.8+ stdlib only. Supports macOS, Linux, and Windows (Claude Code only; Cowork on Windows is untested).

## Quick Start

```bash
# Clone
git clone git@github.com:bryancraven/token-char.git
cd token-char

# Extract all sources to stdout as JSON
python -m token_char.extract

# Extract Cowork only, skip first 2 sessions
python -m token_char.extract --source cowork --skip-first-n 2

# CSV output to a directory
python -m token_char.extract --format csv --output ./out/

# JSONL output to a file
python -m token_char.extract --format jsonl --output data.jsonl

# Extract Codex only
python -m token_char.extract --source codex
```

## CLI Reference

```
python -m token_char.extract [OPTIONS]

  --source {cowork,claude_code,codex,all}  Which source to extract (default: all)
  --cowork-dir PATH                   Override Cowork data directory
  --claude-code-dir PATH              Override Claude Code projects directory
  --codex-dir PATH                    Override Codex sessions directory
  --output PATH                       File or directory (default: stdout)
  --format {json,csv,jsonl}           Output format (default: json)
  --machine NAME                      Machine name override (default: hostname)
  --project-map KEY=VAL               Map dir names to friendly names (repeatable)
  --skip-first-n N                    Skip N oldest Cowork sessions
  --quiet                             Suppress stderr progress
  --version                           Show version
```

## Output Schema

### JSON Envelope

```json
{
  "token_char_version": "0.1.0",
  "extracted_at": "2025-02-12T...",
  "machine": "hostname",
  "sources": ["cowork", "claude_code"],
  "turns": [...],
  "sessions": [...]
}
```

### Turn Fields

| Field | Type | Description |
|---|---|---|
| `source` | str | `"cowork"`, `"claude_code"`, or `"codex"` |
| `machine` | str | Hostname or `--machine` override |
| `project` | str | Project name |
| `session_id` | str | Session UUID |
| `turn_number` | int | 1-indexed within session |
| `timestamp` | str/null | ISO 8601 UTC |
| `model` | str | Full model string |
| `model_family` | str | `"opus"` / `"sonnet"` / `"haiku"` / `"gpt"` / `"unknown"` |
| `input_tokens` | int | Fresh (non-cached) input |
| `output_tokens` | int | Generated output (includes reasoning tokens) |
| `cache_read_tokens` | int | Cached input |
| `cache_create_tokens` | int | Input written to cache |
| `reasoning_output_tokens` | int | Reasoning/thinking output tokens (subset of `output_tokens`, NOT additive in `total_tokens`). 0 for sources without reasoning breakdown. |
| `total_tokens` | int | `input + output + cache_read + cache_create` (reasoning NOT added) |
| `is_subagent` | bool | `true` if turn is from a subagent |
| `subagent_id` | str/null | Agent ID (e.g. `"ab884ec"`) or `null` |

### Session Fields

| Field | Type | Description |
|---|---|---|
| `source` | str | `"cowork"`, `"claude_code"`, or `"codex"` |
| `machine` | str | Hostname |
| `project` | str | Project name |
| `session_id` | str | UUID |
| `title` | str | Session title or `"(untitled)"` |
| `model` | str | Primary model |
| `created_at` | str/null | ISO 8601 |
| `duration_min` | float/null | Duration in minutes |
| `turns_user` | int | Genuine user messages |
| `turns_assistant` | int | Assistant responses |
| `total_input_tokens` | int | Summed |
| `total_output_tokens` | int | Summed |
| `total_cache_read_tokens` | int | Summed |
| `total_cache_create_tokens` | int | Summed |
| `total_reasoning_output_tokens` | int | Summed reasoning tokens (subset of output, NOT additive in total) |
| `total_tokens` | int | Grand total |
| `subagent_turns` | int | Count of subagent assistant turns |

## Remote Extraction

Extract from a remote machine via SSH (no install needed):

```bash
ssh user@host python3 < scripts/remote_extract.py > host.json
```

Configure with environment variables:
- `MACHINE_NAME` — override hostname
- `TC_SOURCE` — `cowork`, `claude_code`, `codex`, or `all` (default: `all`)
- `TC_COWORK_DIR` — override Cowork data directory
- `TC_CLAUDE_CODE_DIR` — override Claude Code projects directory
- `TC_CODEX_DIR` — override Codex sessions directory

## Data Sources

### Cowork (Claude Desktop)

Session data locations:
- **macOS**: `~/Library/Application Support/Claude/local-agent-mode-sessions/<org>/<project>/`
- **Linux**: `~/.config/Claude/local-agent-mode-sessions/<org>/<project>/`
- **Windows**: `%APPDATA%\Claude\local-agent-mode-sessions\<org>\<project>\` (untested)

Files:
- `local_<session_id>.json` — metadata (title, model, timestamps)
- `local_<session_id>/audit.jsonl` — per-message audit log

### Claude Code (CLI)

Session data locations:
- **macOS/Linux**: `~/.claude/projects/<encoded-path>/`
- **Windows**: `%USERPROFILE%\.claude\projects\<encoded-path>\`

On Windows, project directory names encode the full path with the drive letter, e.g. `C--Users-foo-bar` represents `C:\Users\foo\bar`.

Files:
- `<session-id>.jsonl` — main session log
- `<session-id>/subagents/agent-<id>.jsonl` — subagent logs (parsed automatically)

### OpenAI Codex

Session data location (all platforms): `~/.codex/sessions/YYYY/MM/DD/`

Files:
- `rollout-<timestamp>-<session-id>.jsonl` — full session log (metadata, turns, token usage)

Token accounting notes:
- OpenAI's `input_tokens` includes cached tokens; token-char decomposes this into `input_tokens` (fresh) and `cache_read_tokens` (cached)
- OpenAI's `output_tokens` includes reasoning tokens; `reasoning_output_tokens` breaks out the reasoning subset
- `cache_create_tokens` is always 0 (Codex doesn't expose this)

### GitHub Copilot (native VS Code agent)

**Not supported.** GitHub Copilot does not log token usage data locally. There are no local session files with token counts to extract.

## Testing

```bash
pip install pytest
pytest tests/
```

## License

MIT
