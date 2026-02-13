# CLAUDE.md

## What This Is

A standalone, stdlib-only Python toolkit that extracts per-turn token usage data from Claude Desktop (Cowork) and Claude Code (CLI) session logs. Produces structured JSON/CSV/JSONL output at per-turn grain — sufficient to recreate all charts/tables from the PDF reports in the sibling `cowork_usage_report` repo.

## Running

```bash
# Extract all sources to stdout as JSON
python -m token_char.extract

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
│   ├── _common.py     # Shared: timestamp parsing, model_family, platform paths
│   ├── cowork.py      # Cowork (Claude Desktop) parser
│   └── claude_code.py # Claude Code (CLI) parser
├── output.py          # JSON/CSV/JSONL writers
└── extract.py         # CLI entry point (also __main__.py)
```

### Data Flow

1. `extract.py` parses CLI args, resolves platform-specific data directories
2. Source parsers (`cowork.py`, `claude_code.py`) read session files and produce lists of turn/session dicts conforming to `schema.py`
3. `output.py` serializes to the requested format

### Key Patterns

- **Per-turn grain**: Every assistant response is one turn dict with 4 token fields (input, output, cache_read, cache_create) plus `is_subagent`/`subagent_id` for provenance
- **Subagent parsing**: Claude Code sessions may have `<session-id>/subagents/agent-<id>.jsonl` files — these are parsed automatically and their tokens included in session aggregates
- **model_family()**: Substring classification → opus/sonnet/haiku/unknown
- **is_genuine_user_turn()**: Filters out tool_result callback lists from user turn counts
- **Cowork timestamp**: `_audit_timestamp` field (with fallback to `message._audit_timestamp`)
- **Claude Code timestamp**: `timestamp` field directly on the record

### Data Sources

- **Cowork**: `~/Library/Application Support/Claude/local-agent-mode-sessions/<org>/<project>/`
  - `local_<sid>.json` — metadata (title, model, timestamps as epoch-ms)
  - `local_<sid>/audit.jsonl` — per-message audit log
- **Claude Code**: `~/.claude/projects/<encoded-path>/`
  - `<session-id>.jsonl` — main session log
  - `<session-id>/subagents/agent-<id>.jsonl` — subagent logs (parsed automatically)

## Testing

```bash
pytest tests/
```

Tests use synthetic fixtures in `tests/fixtures/`. No real session data needed.
