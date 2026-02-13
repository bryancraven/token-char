# token-char

Extract per-turn token usage data from Claude Desktop (Cowork) and Claude Code (CLI) session logs.

Zero runtime dependencies. Python 3.8+ stdlib only.

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
```

## CLI Reference

```
python -m token_char.extract [OPTIONS]

  --source {cowork,claude_code,all}   Which source to extract (default: all)
  --cowork-dir PATH                   Override Cowork data directory
  --claude-code-dir PATH              Override Claude Code projects directory
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
| `source` | str | `"cowork"` or `"claude_code"` |
| `machine` | str | Hostname or `--machine` override |
| `project` | str | Project name |
| `session_id` | str | Session UUID |
| `turn_number` | int | 1-indexed within session |
| `timestamp` | str/null | ISO 8601 UTC |
| `model` | str | Full model string |
| `model_family` | str | `"opus"` / `"sonnet"` / `"haiku"` / `"unknown"` |
| `input_tokens` | int | Fresh (non-cached) input |
| `output_tokens` | int | Generated output |
| `cache_read_tokens` | int | Cached input |
| `cache_create_tokens` | int | Input written to cache |
| `total_tokens` | int | Sum of four token fields |

### Session Fields

| Field | Type | Description |
|---|---|---|
| `source` | str | `"cowork"` or `"claude_code"` |
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
| `total_tokens` | int | Grand total |

## Remote Extraction

Extract from a remote machine via SSH (no install needed):

```bash
ssh user@host python3 < scripts/remote_extract.py > host.json
```

Configure with environment variables:
- `MACHINE_NAME` — override hostname
- `TC_SOURCE` — `cowork`, `claude_code`, or `all` (default: `all`)
- `TC_COWORK_DIR` — override Cowork data directory
- `TC_CLAUDE_CODE_DIR` — override Claude Code projects directory

## Data Sources

### Cowork (Claude Desktop)

Session data at `~/Library/Application Support/Claude/local-agent-mode-sessions/<org>/<project>/`:
- `local_<session_id>.json` — metadata (title, model, timestamps)
- `local_<session_id>/audit.jsonl` — per-message audit log

### Claude Code (CLI)

Session data at `~/.claude/projects/<encoded-path>/`:
- `<session-id>.jsonl` — session log (project-level only, not subagent dirs)

## Testing

```bash
pip install pytest
pytest tests/
```

## License

MIT
