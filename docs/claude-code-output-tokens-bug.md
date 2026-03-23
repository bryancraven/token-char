# Claude Code / Cowork Output Token Logging Notes

> Filed upstream: [anthropics/claude-code#25941](https://github.com/anthropics/claude-code/issues/25941), see also [#21971](https://github.com/anthropics/claude-code/issues/21971) (independent report confirming the same root cause)

## Summary

Claude-family logs now have **two distinct accounting problems**:

1. Modern Claude Code and Cowork sessions often persist **multiple `assistant` snapshots for one logical response**. If you count each snapshot as a turn, turn counts and input/cache totals are badly inflated.
2. Per-turn `output_tokens` on Claude-family assistant snapshots is often a **placeholder or partial value**, unless final streaming usage is also persisted.

As of **March 23, 2026**, token-char works around the first problem completely and the second problem conservatively:

| Log shape | What is persisted | Safe workaround |
|---|---|---|
| Claude Code older persisted-stream sessions (observed on `2.1.49`) | `assistant` + `stream_event.message_delta` + `result` | Collapse assistant snapshots, recover per-turn output from `message_delta`, recover session output from `result` |
| Claude Code newer snapshot-only sessions (observed on `2.1.78`) | repeated `assistant` snapshots, usually no final `message_delta` / `result` | Collapse assistant snapshots for correct turn/input/cache totals, keep output conservative and mark it unreliable |
| Cowork modern sessions | repeated `assistant` snapshots + `result` records | Collapse assistant snapshots for turn/input/cache totals, recover session output from `result.modelUsage` / `result.usage` |

So the old statement "Claude Code has no workaround" is now too broad. The accurate statement is:

- **Turn counts, input totals, cache-read totals, and cache-create totals can be corrected safely** by collapsing duplicate assistant snapshots.
- **Per-turn output can only be corrected safely when final streaming usage (`message_delta`) is persisted.**
- **Session output can be corrected safely when `result` records are persisted.**

The original February 15, 2026 controlled experiment below is still valid for the underlying placeholder-output bug. What changed is that newer logs also require snapshot deduplication, and a subset of historical logs now persist enough streaming metadata to recover output safely.

## Evidence

### Controlled Experiment

We ran three controlled prompts via `claude -p` and compared the `--output-format json` result (which has the correct `output_tokens`) against the JSONL session log written to disk:

| Prompt | Response | JSON output `output_tokens` | JSONL log `output_tokens` | Visible chars |
|---|---|---|---|---|
| "What is 2+2? Reply with just the number." | "4" | **5** | 1 | 1 |
| "Explain why the sky is blue in 3 sentences." | 3 sentences | **113** | 1 | 509 |
| "Write a binary search function with edge cases." | Full function + explanation | **294** | 2 | 832 |

The JSONL consistently records `output_tokens` as 1-2 regardless of actual response length. The real values (5, 113, 294) are only in the JSON result output.

**Input tokens are accurate.** The discrepancy is limited to `output_tokens`:

| Field | JSON output | JSONL log | Match? |
|---|---|---|---|
| `input_tokens` | 3 | 3 | Yes |
| `cache_creation_input_tokens` | 4,678 | 4,678 | Yes |
| `cache_read_input_tokens` | 17,997 | 17,997 | Yes |
| `output_tokens` | **113** | **1** | **No** |

### Stream-JSON Analysis

Capturing `--output-format stream-json` reveals the mechanism. There are two events with usage data:

1. **`assistant` event** (early in stream): `output_tokens: 1` — **this is what gets written to the JSONL**
2. **`result` event** (end of stream): `output_tokens: 118` — **this has the real value but is never persisted**

That was accurate for the February 15, 2026 corpus used in the experiment. In the March 23, 2026 local corpus, the situation is mixed: one older `2.1.49` session persisted `stream_event` and `result`, while newer `2.1.78` sessions persisted repeated `assistant` snapshots without final streaming usage.

### Cache Delta Validation

To confirm the real output tokens are correct (and not inflated), we ran a 3-turn conversation and checked whether the previous turn's output shows up in the next turn's cache accounting:

| Turn | Real `output_tokens` | Next turn `cache_create` delta | Residual (delta - output) |
|---|---|---|---|
| 0 | 891 | +909 | 18 (= new user prompt tokens) |
| 1 | 191 | +207 (net of cache promotion) | 16 (= new user prompt tokens) |

The cache system accounts for the **real** output tokens with near-perfect precision. The residuals of 16-18 tokens correspond exactly to the user prompt text being tokenized. This confirms the JSON output values are correct and the JSONL values are wrong.

### Scale of Impact

In a typical Claude Code session (147 assistant turns), scanning existing logs shows:
- JSONL `output_tokens` values: consistently 1-9 per turn
- `chars/token` ratio using JSONL values: median **67** (should be ~4)
- Estimated real output tokens (from visible text alone): 10-100x higher than logged

### Can Real Output Tokens Be Recovered From Logs?

**For pure conversational sessions** (no tool use): Yes, approximately. The cache delta between consecutive turns captures the previous turn's real output plus new user input. Subtracting estimated user prompt tokens gives a reasonable estimate for all-but-the-last turn.

**For agentic sessions with tool use** (the majority of Claude Code usage): No. Tool results (file contents, command output) are fed back as user records between API calls and dominate the cache deltas. In a real session we measured, tool results accounted for **96% of inter-turn content** (~52K estimated tokens), making it impossible to isolate the model's output tokens from the cache growth.

**For the last turn in any session**: No next turn exists to measure cache delta against.

## Cowork (Claude Desktop) Has The Same Output Bug

Cowork per-turn `output_tokens` exhibits the same placeholder pattern as Claude Code. In one 139-turn Cowork session, per-turn `output_tokens` ranged from 1-27 (median 3), summing to 2,788, while `result` records reported 88,769.

Unlike snapshot-only Claude Code sessions, Cowork usually persists `result` records, so session-level output totals can be corrected even when per-turn output remains unreliable.

## Status In March 2026

The underlying output-token bug is **not fixed**. Some Claude Code assistant snapshots now report plausible `output_tokens`, but many still report placeholder values, especially in streaming/tool-use flows. The newer and more severe practical issue is that both Claude Code and Cowork now emit repeated assistant snapshots for one logical response, so naive row-counting inflates turn/input/cache totals even before you get to the output-token bug.

## Environment (original investigation)

- Claude Code version: 2.1.42
- Model: claude-opus-4-6
- Platform: macOS (Darwin 25.2.0)
- Date: 2026-02-15

## Reproduction

```bash
# Run a simple prompt and compare JSON output vs JSONL log
claude -p "Explain why the sky is blue in 3 sentences." --output-format json 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'JSON output_tokens: {d[\"usage\"][\"output_tokens\"]}')
print(f'Session ID: {d[\"session_id\"]}')
"

# Then inspect the JSONL:
# python3 -c "
# import json, glob
# for line in open(glob.glob('~/.claude/projects/*/<SESSION_ID>.jsonl')[0]):
#     rec = json.loads(line)
#     if rec.get('type') == 'assistant':
#         print(f'JSONL output_tokens: {rec[\"message\"][\"usage\"][\"output_tokens\"]}')
# "
```

## Suggested Fix

Write the final `output_tokens` from the `message_delta` or `result` streaming event back to the assistant record in the JSONL, or append a separate `result`-type record with the final usage. All other usage fields (`input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`) are already accurate — only `output_tokens` needs the final-value writeback.

## Experiment Script

The full experiment script is available at [`scripts/token_audit.py`](../scripts/token_audit.py) in the token-char repository.
