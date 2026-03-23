# token-char

Extract per-turn token usage data from Claude Desktop (Cowork), Claude Code (CLI), and OpenAI Codex (CLI, Desktop app, and VS Code extension) session logs. Both Claude Code and Codex support VS Code extension sessions. **GitHub Copilot is not supported** — it does not log token usage locally.

Zero runtime dependencies. Python 3.8+ stdlib only. Supports macOS, Linux, and Windows.

**Note:** Modern Claude-family logs have two separate issues: repeated `assistant` snapshots for one logical response, and unreliable per-turn `output_tokens` unless final streaming usage is persisted. token-char now dedupes logical responses for Claude Code and Cowork, corrects Cowork session output totals from `result` records, and marks output reliability explicitly with `*_output_tokens_reliable` / `*_output_tokens_source`. See [Known Limitations](#known-limitations) for details.

## Quick Start

```bash
# Clone (HTTPS — works behind corporate firewalls)
git clone https://github.com/bryancraven/token-char.git
cd token-char

# Or via SSH
# git clone git@github.com:bryancraven/token-char.git

# Extract all sources to stdout as JSON
python -m token_char.extract

# Extract Cowork only, skip first 2 sessions
python -m token_char.extract --source cowork --skip-first-n 2

# CSV output to a directory
python -m token_char.extract --format csv --output ./out/

# JSONL output to a file
python -m token_char.extract --format jsonl --output data.jsonl

# Human-readable terminal summary
python -m token_char.extract --format table

# Extract Codex only (CLI + Desktop app + VS Code sessions)
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
  --format {json,csv,jsonl,table}     Output format (default: json)
  --detail {sessions,all}             Detail level for table format (default: sessions)
  --ascii                             Force ASCII output (no Unicode box-drawing chars)
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
| `output_tokens` | int | Generated output (includes reasoning tokens). Use `output_tokens_reliable` and `output_tokens_source` before deriving methodology-sensitive stats. |
| `output_tokens_reliable` | bool | `true` when per-turn output came from an authoritative source (`message_delta` for Claude Code, API usage for Codex) |
| `output_tokens_source` | str | Where per-turn output came from: `assistant_snapshot`, `message_delta`, `result`, or `api_usage` |
| `cache_read_tokens` | int | Cached input |
| `cache_create_tokens` | int | Input written to cache |
| `reasoning_output_tokens` | int | Reasoning/thinking output tokens (subset of `output_tokens`, NOT additive in `total_tokens`). 0 for sources without reasoning breakdown. |
| `total_tokens` | int | `input + output + cache_read + cache_create` (reasoning NOT added). Per-turn totals inherit `output_tokens` reliability. |
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
| `total_output_tokens` | int | Summed session output |
| `total_output_tokens_reliable` | bool | `true` when session output came from `result`, complete `message_delta`, or API usage |
| `total_output_tokens_source` | str | Source of the session output total: `assistant_snapshot`, `message_delta`, `result`, or `api_usage` |
| `total_cache_read_tokens` | int | Summed |
| `total_cache_create_tokens` | int | Summed |
| `total_reasoning_output_tokens` | int | Summed reasoning tokens (subset of output, NOT additive in total) |
| `total_tokens` | int | Grand total using the session-level output total above |
| `total_cost_usd` | float/null | Session cost in USD. Available for Cowork sessions with `result` records; `null` for Claude Code and Codex |
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

### OpenAI Codex (CLI, Desktop App, VS Code Extension)

All three Codex clients — CLI (`codex_cli_rs`), Desktop app (`Codex Desktop`), and VS Code extension (`codex_vscode`) — write session logs to the same location.

Session data location (all platforms): `~/.codex/sessions/YYYY/MM/DD/`

Files:
- `rollout-<timestamp>-<session-id>.jsonl` — full session log (metadata, turns, token usage)

**Note:** The Codex Desktop app also stores data in `~/Library/Application Support/Codex/` (macOS), but that directory contains only Electron app state (binary). The actual session JSONL logs are in `~/.codex/sessions/`.

Turn boundary protocols (parser auto-detects):
- **Newer CLI**: `task_started`/`task_complete` event pairs — each pair = one turn (per-task granularity)
- **Desktop app / VS Code / older CLI**: No task boundary events. Parser uses non-zero `token_count` deltas — each = one API round-trip (per-API-call granularity, typically much finer than per-task)

The `session_meta.originator` field identifies the client: `"Codex Desktop"`, `"codex_vscode"`, or `"codex_cli_rs"`.

Token accounting notes:
- OpenAI's `input_tokens` includes cached tokens; token-char decomposes this into `input_tokens` (fresh) and `cache_read_tokens` (cached)
- OpenAI's `output_tokens` includes reasoning tokens; `reasoning_output_tokens` breaks out the reasoning subset
- `cache_create_tokens` is always 0 (Codex doesn't expose this)

### GitHub Copilot (native VS Code agent)

**Not supported.** GitHub Copilot does not log token usage data locally. There are no local session files with token counts to extract.

### Cross-source field mapping

All three sources produce the same unified schema, but the underlying data differs. Here's what maps cleanly and what doesn't.

**What maps cleanly across all sources:**
- `input_tokens` — all sources provide this (Codex bundles cached inside input, so we decompose: `codex_input - codex_cached = our_input`)
- `output_tokens` — all sources expose an output field, but Claude-family sources may require reliability checks (`*_output_tokens_reliable`, `*_output_tokens_source`)
- `cache_read_tokens` — all sources provide this (Codex calls it `cached_input_tokens`)
- `session_id`, `timestamp`, `model`, `project` — present in all sources
- Turn structure — all have clear logical turn boundaries (Claude-family sources may require collapsing repeated assistant snapshots first; Codex is per-API-call or per-task depending on client)
- Session structure — all have one session per file

**Key differences between sources:**

| | Claude (Cowork / Code) | Codex |
|---|---|---|
| `cache_create_tokens` | Real values (can be significant) | **Always 0** — Codex doesn't expose this |
| `reasoning_output_tokens` | Always 0 (not available) | Real values — subset of `output_tokens` |
| Turn granularity | Each API response = 1 turn (tool-use loops produce multiple turns per user message) | Varies by client: per-API-call for Desktop/VSCode (comparable to Claude), per-task for newer CLI (coarser) |
| Subagents | Separate `.jsonl` files, tracked via `is_subagent`/`subagent_id` | Not exposed in session logs |
| Token reporting | Per-response absolute values | Cumulative session totals — parser computes deltas |
| `turns_user` vs `turns_assistant` | Can differ (e.g. 1 user message triggers 6 assistant turns) | Differ for Desktop/VSCode (many API-call turns per user message); equal for CLI per-task protocol |

**Biggest gap: `cache_create_tokens`**

Codex provides no visibility into cache *writes*. For Claude, `cache_create_tokens` can be significant (e.g. 19k tokens in a single session). For Codex this is always 0 — not because nothing is cached, but because the OpenAI API doesn't report it. This means Codex `total_tokens` may slightly understate real usage if cache creation cost matters to your analysis.

**Turn granularity difference**

Claude Code fires one turn per API round-trip, so a tool-use loop of 6 API calls produces 6 turns under 1 user message.

Codex turn granularity depends on the client:
- **Desktop app / VS Code extension**: The parser extracts per-API-call turns from `token_count` deltas. This is comparable to Claude Code's granularity — a session with 4 user messages may produce 87 turns (one per API call).
- **Newer CLI** (`codex_cli_rs` with `task_started`/`task_complete`): Wraps the entire user task into a single turn. This is coarser — each turn is more like a "session segment."

The parser auto-detects which protocol a session uses. Token totals are exact regardless of protocol; only the per-turn breakdown differs.

## Known Limitations

### Claude-Family Logging Is Mixed

Claude Code and Cowork both emit repeated `assistant` snapshots for one logical response in modern logs. If those rows are counted naively, turn counts and input/cache totals are badly inflated. token-char now collapses those snapshots into one logical response using `requestId` / `message.id` / `uuid` identities and takes the max observed input/cache counters for that response.

Per-turn `output_tokens` is still source-dependent:

- **Claude Code older persisted-stream sessions**: if `stream_event.message_delta.usage.output_tokens` is present, token-char uses it for authoritative per-turn output. If `result` is present, session totals come from `result`.
- **Claude Code newer snapshot-only sessions**: per-turn output remains conservative and is marked unreliable because no final usage is persisted.
- **Cowork**: per-turn output remains unreliable, but session totals are corrected from `result.modelUsage` / `result.usage` when present.
- **Codex**: API usage is authoritative for both per-turn and session totals.

Use `output_tokens_reliable` / `output_tokens_source` and `total_output_tokens_reliable` / `total_output_tokens_source` before computing derived output-based metrics.

See [docs/claude-code-output-tokens-bug.md](docs/claude-code-output-tokens-bug.md) for the full investigation, controlled experiment results, and reproduction steps. Filed upstream: [anthropics/claude-code#25941](https://github.com/anthropics/claude-code/issues/25941), see also [#21971](https://github.com/anthropics/claude-code/issues/21971)

## Testing

```bash
pip install pytest
pytest tests/
```

## License

MIT
