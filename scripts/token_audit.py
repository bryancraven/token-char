#!/usr/bin/env python3
"""
Token Accounting Experiment — Reverse-Engineering Claude Code Logs

Investigates where extended thinking tokens appear in Claude Code JSONL logs.

Hypothesis: thinking tokens are NOT in output_tokens but may be folded into
cache_creation_input_tokens on subsequent turns.

Usage:
    # Phase 1: Generate test commands to run in another terminal
    python3 scripts/token_audit.py generate

    # Phase 2: Analyze resulting sessions
    python3 scripts/token_audit.py analyze <session_id_A> <session_id_B> <session_id_C>

    # Analyze a single existing session in detail
    python3 scripts/token_audit.py analyze-session <session_id>

    # Analyze the multi-turn test sessions
    python3 scripts/token_audit.py analyze-multiturn <session_id>

    # Analyze stream-json capture file
    python3 scripts/token_audit.py analyze-stream <capture_file>
"""

import json
import glob
import os
import sys
import textwrap
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECTS_DIR = Path.home() / ".claude" / "projects"

# Test prompts for controlled experiments
PROMPTS = {
    "A": {
        "label": "minimal thinking",
        "prompt": "What is 2+2? Reply with just the number.",
    },
    "B": {
        "label": "moderate thinking",
        "prompt": "Explain why the sky is blue in exactly 3 sentences.",
    },
    "C": {
        "label": "heavy thinking",
        "prompt": "Write a Python function that implements binary search. Include edge cases.",
    },
}

# Multi-turn conversation for cache growth analysis
MULTITURN_PROMPTS = [
    "Explain the difference between a stack and a queue. Be thorough.",
    "Now compare their time complexities for common operations in a markdown table.",
    "Write Python implementations of both with type hints and docstrings.",
]


# ---------------------------------------------------------------------------
# Session file discovery
# ---------------------------------------------------------------------------

def find_session_file(session_id):
    """Find JSONL file for a session ID across all projects."""
    pattern = str(PROJECTS_DIR / "*" / f"{session_id}.jsonl")
    matches = glob.glob(pattern)
    if matches:
        return matches[0]
    # Also check if session_id is a full path
    if os.path.isfile(session_id):
        return session_id
    return None


# ---------------------------------------------------------------------------
# JSONL parsing — extracts everything we need for the audit
# ---------------------------------------------------------------------------

def parse_session(filepath):
    """Parse a Claude Code JSONL session file into structured turn data.

    Returns list of dicts, one per assistant turn, with:
        - turn_number: 0-based index among assistant turns
        - record_index: 0-based index in the raw JSONL file
        - type: 'assistant'
        - usage: full usage dict (including nested objects)
        - usage_flat: flattened token counts (input, output, cache_read, cache_create)
        - cache_creation_breakdown: {ephemeral_5m, ephemeral_1h} or None
        - server_tool_use: dict or None
        - content_blocks: list of {type, text_len, name} per content block
        - has_thinking: bool — whether a thinking block was present
        - thinking_text_len: int — character count of thinking text (0 if redacted)
        - visible_text_len: int — character count of non-thinking text blocks
        - tool_uses: list of tool names used
        - model: model name string
        - timestamp: raw timestamp string
        - is_subagent: bool
    """
    turns = []
    user_messages = []  # track user messages for context
    turn_number = 0

    with open(filepath) as f:
        for record_index, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            rec_type = rec.get("type")

            if rec_type == "user":
                content = rec.get("message", {}).get("content", "")
                if isinstance(content, str):
                    user_messages.append(content)
                elif isinstance(content, list):
                    # Skip tool_result-only messages
                    texts = [
                        c.get("text", "")
                        for c in content
                        if isinstance(c, dict) and c.get("type") != "tool_result"
                    ]
                    if texts:
                        user_messages.append(" ".join(texts))

            elif rec_type == "assistant":
                msg = rec.get("message", {})
                usage = msg.get("usage", {})
                content = msg.get("content", [])

                # Flatten core token fields
                usage_flat = {
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
                    "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
                }

                # Nested cache_creation breakdown
                cache_creation = usage.get("cache_creation")
                cache_breakdown = None
                if isinstance(cache_creation, dict):
                    cache_breakdown = {
                        "ephemeral_5m": cache_creation.get("ephemeral_5m_input_tokens", 0),
                        "ephemeral_1h": cache_creation.get("ephemeral_1h_input_tokens", 0),
                    }

                # Server tool use
                server_tool = usage.get("server_tool_use")

                # Content analysis
                content_blocks = []
                has_thinking = False
                thinking_text_len = 0
                visible_text_len = 0
                tool_uses = []

                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        btype = block.get("type", "")
                        btext = block.get("text", "")
                        bname = block.get("name", "")

                        if btype == "thinking":
                            has_thinking = True
                            thinking_text_len += len(btext)
                            content_blocks.append({
                                "type": "thinking",
                                "text_len": len(btext),
                            })
                        elif btype == "text":
                            visible_text_len += len(btext)
                            content_blocks.append({
                                "type": "text",
                                "text_len": len(btext),
                            })
                        elif btype == "tool_use":
                            tool_uses.append(bname)
                            content_blocks.append({
                                "type": "tool_use",
                                "name": bname,
                            })
                        else:
                            content_blocks.append({"type": btype})

                turn = {
                    "turn_number": turn_number,
                    "record_index": record_index,
                    "type": "assistant",
                    "usage": usage,
                    "usage_flat": usage_flat,
                    "cache_creation_breakdown": cache_breakdown,
                    "server_tool_use": server_tool,
                    "content_blocks": content_blocks,
                    "has_thinking": has_thinking,
                    "thinking_text_len": thinking_text_len,
                    "visible_text_len": visible_text_len,
                    "tool_uses": tool_uses,
                    "model": msg.get("model", ""),
                    "timestamp": rec.get("timestamp", ""),
                    "is_subagent": rec.get("isSidechain", False),
                    "preceding_user_msg": user_messages[-1] if user_messages else "",
                    # Extra usage fields for inspection
                    "service_tier": usage.get("service_tier", ""),
                    "speed": usage.get("speed", ""),
                }
                turns.append(turn)
                turn_number += 1

    return turns


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def fmt_num(n):
    """Format number with commas."""
    if n is None:
        return "-"
    return f"{n:,}"


def print_table(headers, rows, col_widths=None):
    """Print a simple aligned table."""
    if col_widths is None:
        col_widths = []
        for i, h in enumerate(headers):
            w = len(str(h))
            for row in rows:
                if i < len(row):
                    w = max(w, len(str(row[i])))
            col_widths.append(w)

    # Header
    header_line = "  ".join(str(h).rjust(w) for h, w in zip(headers, col_widths))
    print(header_line)
    print("  ".join("-" * w for w in col_widths))

    # Rows
    for row in rows:
        cells = []
        for i, (val, w) in enumerate(zip(row, col_widths)):
            cells.append(str(val).rjust(w))
        print("  ".join(cells))


# ---------------------------------------------------------------------------
# Subcommand: generate
# ---------------------------------------------------------------------------

def cmd_generate():
    """Print the shell commands to run the controlled experiments."""
    print("=" * 72)
    print("TOKEN AUDIT — Phase 1: Generate Test Commands")
    print("=" * 72)
    print()
    print("Run these commands in a SEPARATE terminal (not inside Claude Code).")
    print("Each command produces a JSON result with a session_id.")
    print("Record the session IDs, then run the analyze phase.")
    print()

    print("--- Single-prompt tests ---")
    print()
    for key, info in PROMPTS.items():
        prompt = info["prompt"]
        label = info["label"]
        print(f"# Prompt {key} ({label}):")
        print(f'claude -p "{prompt}" --output-format json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\\"Session {key}: {{d.get(\\\"session_id\\\", \\\"???\\\")}}\\")"')
        print()

    print()
    print("--- Multi-turn cache growth test ---")
    print()
    print("# Turn 1:")
    print(f'claude -p "{MULTITURN_PROMPTS[0]}" --output-format json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\\"Multi-turn session: {{d.get(\\\"session_id\\\", \\\"???\\\")}}\\")"')
    print()
    print("# Turn 2 (replace SESSION_ID with the ID from Turn 1):")
    print(f'claude -p "{MULTITURN_PROMPTS[1]}" --resume SESSION_ID --output-format json 2>/dev/null')
    print()
    print("# Turn 3 (same SESSION_ID):")
    print(f'claude -p "{MULTITURN_PROMPTS[2]}" --resume SESSION_ID --output-format json 2>/dev/null')
    print()

    print()
    print("--- Stream analysis test ---")
    print()
    print("# Capture stream-json output to a file for analysis:")
    prompt = PROMPTS["B"]["prompt"]
    print(f'claude -p "{prompt}" --output-format stream-json --verbose 2>&1 | tee /tmp/stream_capture.jsonl')
    print()
    print("# Then analyze:")
    print("python3 scripts/token_audit.py analyze-stream /tmp/stream_capture.jsonl")
    print()

    print("=" * 72)
    print("After running, use:")
    print("  python3 scripts/token_audit.py analyze <sid_A> <sid_B> <sid_C>")
    print("  python3 scripts/token_audit.py analyze-multiturn <sid>")
    print("  python3 scripts/token_audit.py analyze-stream /tmp/stream_capture.jsonl")
    print("=" * 72)


# ---------------------------------------------------------------------------
# Subcommand: analyze-session
# ---------------------------------------------------------------------------

def cmd_analyze_session(session_id):
    """Deep analysis of a single session."""
    filepath = find_session_file(session_id)
    if not filepath:
        print(f"ERROR: Could not find session file for {session_id}", file=sys.stderr)
        print(f"Searched: {PROJECTS_DIR}/*/{session_id}.jsonl", file=sys.stderr)
        sys.exit(1)

    print(f"Session: {session_id}")
    print(f"File:    {filepath}")
    print()

    turns = parse_session(filepath)
    if not turns:
        print("No assistant turns found.")
        return

    print(f"Total assistant turns: {len(turns)}")
    print(f"Model: {turns[0]['model']}")
    print()

    # Per-turn breakdown table
    headers = [
        "Turn", "Think?", "ThinkCh", "VisCh", "OutTok",
        "InpTok", "CacheRd", "CacheCr", "Eph5m", "Eph1h",
        "Tools", "Content"
    ]
    rows = []
    for t in turns:
        uf = t["usage_flat"]
        cb = t["cache_creation_breakdown"] or {}
        content_desc = []
        for b in t["content_blocks"]:
            if b["type"] == "thinking":
                content_desc.append(f"think({b['text_len']}ch)")
            elif b["type"] == "text":
                content_desc.append(f"text({b['text_len']}ch)")
            elif b["type"] == "tool_use":
                content_desc.append(b.get("name", "tool"))
            else:
                content_desc.append(b["type"])

        rows.append([
            t["turn_number"],
            "Y" if t["has_thinking"] else "",
            fmt_num(t["thinking_text_len"]) if t["thinking_text_len"] else "",
            fmt_num(t["visible_text_len"]) if t["visible_text_len"] else "",
            fmt_num(uf["output_tokens"]),
            fmt_num(uf["input_tokens"]),
            fmt_num(uf["cache_read_input_tokens"]),
            fmt_num(uf["cache_creation_input_tokens"]),
            fmt_num(cb.get("ephemeral_5m", "")),
            fmt_num(cb.get("ephemeral_1h", "")),
            ",".join(t["tool_uses"]) if t["tool_uses"] else "",
            " | ".join(content_desc)[:60],
        ])

    print_table(headers, rows)

    # Summary statistics
    print()
    print("--- Token Totals ---")
    totals = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}
    for t in turns:
        uf = t["usage_flat"]
        totals["input"] += uf["input_tokens"]
        totals["output"] += uf["output_tokens"]
        totals["cache_read"] += uf["cache_read_input_tokens"]
        totals["cache_create"] += uf["cache_creation_input_tokens"]
    grand = sum(totals.values())
    for k, v in totals.items():
        pct = (v / grand * 100) if grand else 0
        print(f"  {k:>15s}: {fmt_num(v):>12s}  ({pct:.1f}%)")
    print(f"  {'TOTAL':>15s}: {fmt_num(grand):>12s}")

    # Thinking analysis
    thinking_turns = [t for t in turns if t["has_thinking"]]
    if thinking_turns:
        print()
        print(f"--- Thinking Analysis ({len(thinking_turns)} turns with thinking) ---")
        for t in thinking_turns:
            uf = t["usage_flat"]
            print(
                f"  Turn {t['turn_number']}: "
                f"thinking_chars={t['thinking_text_len']}, "
                f"visible_chars={t['visible_text_len']}, "
                f"output_tokens={uf['output_tokens']}, "
                f"cache_create={uf['cache_creation_input_tokens']}"
            )

        # Check if thinking text is redacted
        all_redacted = all(t["thinking_text_len"] == 0 for t in thinking_turns)
        if all_redacted:
            print()
            print("  NOTE: All thinking text is REDACTED (0 chars) in the JSONL logs.")
            print("  Cannot correlate thinking length with token counts from logs alone.")
            print("  Use stream-json capture for un-redacted thinking text.")

    # Cache growth analysis
    print()
    print("--- Cache Growth Across Turns ---")
    cache_headers = ["Turn", "CacheCr", "CacheRd", "Delta_Cr", "Delta_Rd", "Think?", "OutTok"]
    cache_rows = []
    prev_cr = 0
    prev_rd = 0
    for t in turns:
        uf = t["usage_flat"]
        cr = uf["cache_creation_input_tokens"]
        rd = uf["cache_read_input_tokens"]
        delta_cr = cr - prev_cr
        delta_rd = rd - prev_rd
        cache_rows.append([
            t["turn_number"],
            fmt_num(cr),
            fmt_num(rd),
            f"{delta_cr:+,}",
            f"{delta_rd:+,}",
            "Y" if t["has_thinking"] else "",
            fmt_num(uf["output_tokens"]),
        ])
        prev_cr = cr
        prev_rd = rd

    print_table(cache_headers, cache_rows)

    # Ephemeral cache breakdown
    turns_with_breakdown = [t for t in turns if t["cache_creation_breakdown"]]
    if turns_with_breakdown:
        print()
        print("--- Ephemeral Cache Breakdown ---")
        eph_headers = ["Turn", "CacheCr_Total", "Eph_5m", "Eph_1h", "Sum_Check"]
        eph_rows = []
        for t in turns_with_breakdown:
            uf = t["usage_flat"]
            cb = t["cache_creation_breakdown"]
            total = uf["cache_creation_input_tokens"]
            e5 = cb["ephemeral_5m"]
            e1 = cb["ephemeral_1h"]
            check = "OK" if e5 + e1 == total else f"MISMATCH ({e5 + e1})"
            eph_rows.append([
                t["turn_number"],
                fmt_num(total),
                fmt_num(e5),
                fmt_num(e1),
                check,
            ])
        print_table(eph_headers, eph_rows)

    # Output token vs visible text ratio
    text_turns = [t for t in turns if t["visible_text_len"] > 0]
    if text_turns:
        print()
        print("--- Output Tokens vs Visible Text ---")
        ratio_headers = ["Turn", "OutTok", "VisCh", "Ch/Tok", "Think?"]
        ratio_rows = []
        for t in text_turns:
            uf = t["usage_flat"]
            out = uf["output_tokens"]
            vis = t["visible_text_len"]
            ratio = vis / out if out else float("inf")
            ratio_rows.append([
                t["turn_number"],
                fmt_num(out),
                fmt_num(vis),
                f"{ratio:.1f}",
                "Y" if t["has_thinking"] else "",
            ])
        print_table(ratio_headers, ratio_rows)

    # Raw usage dump for first few turns
    print()
    print("--- Raw Usage (first 5 turns) ---")
    for t in turns[:5]:
        print(f"  Turn {t['turn_number']}:")
        print(f"    {json.dumps(t['usage'], indent=4, default=str)}")
        print()


# ---------------------------------------------------------------------------
# Subcommand: analyze (compare 3 prompts)
# ---------------------------------------------------------------------------

def cmd_analyze(session_ids):
    """Compare token accounting across controlled prompt sessions."""
    if len(session_ids) < 2:
        print("Need at least 2 session IDs to compare.", file=sys.stderr)
        sys.exit(1)

    print("=" * 72)
    print("TOKEN AUDIT — Controlled Prompt Comparison")
    print("=" * 72)
    print()

    labels = list(PROMPTS.keys())
    results = []

    for i, sid in enumerate(session_ids):
        label = labels[i] if i < len(labels) else f"#{i}"
        filepath = find_session_file(sid)
        if not filepath:
            print(f"WARNING: Could not find session {sid}", file=sys.stderr)
            continue

        turns = parse_session(filepath)
        if not turns:
            print(f"WARNING: No turns in session {sid}", file=sys.stderr)
            continue

        # For single-prompt sessions, we expect 1 assistant turn
        # but take the last one in case there are system turns
        t = turns[-1]
        uf = t["usage_flat"]
        prompt_info = PROMPTS.get(label, {})

        results.append({
            "label": label,
            "prompt_desc": prompt_info.get("label", "?"),
            "session_id": sid,
            "output_tokens": uf["output_tokens"],
            "input_tokens": uf["input_tokens"],
            "cache_read": uf["cache_read_input_tokens"],
            "cache_create": uf["cache_creation_input_tokens"],
            "visible_chars": t["visible_text_len"],
            "thinking_chars": t["thinking_text_len"],
            "has_thinking": t["has_thinking"],
            "total": uf["input_tokens"] + uf["output_tokens"]
                     + uf["cache_read_input_tokens"]
                     + uf["cache_creation_input_tokens"],
            "num_turns": len(turns),
        })

    if not results:
        print("No valid sessions found.")
        return

    # Comparison table
    headers = [
        "Prompt", "Type", "OutTok", "VisCh", "ThinkCh", "CacheCr",
        "CacheRd", "InpTok", "Total", "Think?"
    ]
    rows = []
    for r in results:
        rows.append([
            r["label"],
            r["prompt_desc"],
            fmt_num(r["output_tokens"]),
            fmt_num(r["visible_chars"]),
            fmt_num(r["thinking_chars"]),
            fmt_num(r["cache_create"]),
            fmt_num(r["cache_read"]),
            fmt_num(r["input_tokens"]),
            fmt_num(r["total"]),
            "Y" if r["has_thinking"] else "N",
        ])
    print_table(headers, rows)

    # Analysis
    print()
    print("--- Analysis ---")
    for r in results:
        out = r["output_tokens"]
        vis = r["visible_chars"]
        ratio = vis / out if out else float("inf")
        print(
            f"  Prompt {r['label']} ({r['prompt_desc']}): "
            f"chars/token ratio = {ratio:.1f}, "
            f"thinking_present = {r['has_thinking']}"
        )

    print()
    print("If output_tokens includes thinking, we'd expect much higher output_tokens")
    print("for thinking-heavy prompts relative to visible text length.")
    print("If chars/token ratio is ~3-5 consistently, output_tokens likely reflects")
    print("visible text only (or visible + thinking if ratio drops for thinking turns).")


# ---------------------------------------------------------------------------
# Subcommand: analyze-multiturn
# ---------------------------------------------------------------------------

def cmd_analyze_multiturn(session_id):
    """Analyze cache growth across turns in a multi-turn session."""
    filepath = find_session_file(session_id)
    if not filepath:
        print(f"ERROR: Could not find session {session_id}", file=sys.stderr)
        sys.exit(1)

    print("=" * 72)
    print("TOKEN AUDIT — Multi-Turn Cache Growth Analysis")
    print("=" * 72)
    print()
    print(f"Session: {session_id}")
    print(f"File:    {filepath}")
    print()

    turns = parse_session(filepath)
    if not turns:
        print("No assistant turns found.")
        return

    # Per-turn cache analysis
    headers = [
        "Turn", "OutTok", "CacheCr", "CacheRd", "InpTok",
        "Delta_CacheCr", "Delta_CacheRd", "Think?", "VisCh"
    ]
    rows = []
    prev_cr = 0
    prev_rd = 0
    for t in turns:
        uf = t["usage_flat"]
        cr = uf["cache_creation_input_tokens"]
        rd = uf["cache_read_input_tokens"]
        rows.append([
            t["turn_number"],
            fmt_num(uf["output_tokens"]),
            fmt_num(cr),
            fmt_num(rd),
            fmt_num(uf["input_tokens"]),
            f"{cr - prev_cr:+,}",
            f"{rd - prev_rd:+,}",
            "Y" if t["has_thinking"] else "",
            fmt_num(t["visible_text_len"]),
        ])
        prev_cr = cr
        prev_rd = rd

    print_table(headers, rows)

    # Growth analysis
    print()
    print("--- Cache Growth Interpretation ---")
    print()
    print("If thinking tokens are folded into cache on the next turn, we expect:")
    print("  cache_create[N+1] or cache_read[N+1] to spike after thinking-heavy turns.")
    print()
    print("Observed pattern:")
    for i, t in enumerate(turns):
        uf = t["usage_flat"]
        if i > 0:
            prev = turns[i - 1]
            prev_uf = prev["usage_flat"]
            cr_growth = uf["cache_creation_input_tokens"] - prev_uf["cache_creation_input_tokens"]
            rd_growth = uf["cache_read_input_tokens"] - prev_uf["cache_read_input_tokens"]
            prev_out = prev_uf["output_tokens"]
            print(
                f"  Turn {prev['turn_number']}→{t['turn_number']}: "
                f"prev_output_tokens={prev_out}, "
                f"cache_create_growth={cr_growth:+,}, "
                f"cache_read_growth={rd_growth:+,}"
            )
            # If cache_read growth >> prev output_tokens, thinking may be in cache
            if prev_out > 0 and rd_growth > prev_out * 5:
                print(
                    f"    ⚠ cache_read grew {rd_growth/prev_out:.0f}x more than "
                    f"prev output_tokens — possible thinking in cache"
                )


# ---------------------------------------------------------------------------
# Subcommand: analyze-stream
# ---------------------------------------------------------------------------

def cmd_analyze_stream(filepath):
    """Analyze a stream-json capture file for hidden token fields."""
    if not os.path.isfile(filepath):
        print(f"ERROR: File not found: {filepath}", file=sys.stderr)
        sys.exit(1)

    print("=" * 72)
    print("TOKEN AUDIT — Stream-JSON Analysis")
    print("=" * 72)
    print()
    print(f"File: {filepath}")
    print()

    events = []
    usage_events = []
    all_event_types = set()
    all_keys_seen = set()
    thinking_events = []

    with open(filepath) as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            events.append(rec)
            event_type = rec.get("type", rec.get("event", "unknown"))
            all_event_types.add(event_type)

            # Collect all keys recursively
            _collect_keys(rec, "", all_keys_seen)

            # Look for usage data
            if "usage" in rec:
                usage_events.append({"line": line_num, "usage": rec["usage"], "type": event_type})
            # Also check nested message.usage
            msg = rec.get("message", {})
            if isinstance(msg, dict) and "usage" in msg:
                usage_events.append({"line": line_num, "usage": msg["usage"], "type": event_type})

            # Look for thinking-related content
            if "thinking" in str(rec).lower():
                thinking_events.append({"line": line_num, "type": event_type, "rec": rec})

    print(f"Total events: {len(events)}")
    print(f"Event types: {sorted(all_event_types)}")
    print()

    # All unique key paths
    print("--- All Key Paths Seen ---")
    for key in sorted(all_keys_seen):
        print(f"  {key}")
    print()

    # Usage events
    if usage_events:
        print(f"--- Usage Events ({len(usage_events)}) ---")
        for ue in usage_events:
            print(f"  Line {ue['line']} ({ue['type']}):")
            print(f"    {json.dumps(ue['usage'], indent=4, default=str)}")
            print()
    else:
        print("No usage events found in stream.")
        print()

    # Thinking events
    if thinking_events:
        print(f"--- Thinking-Related Events ({len(thinking_events)}) ---")
        for te in thinking_events[:10]:  # Limit to first 10
            print(f"  Line {te['line']} ({te['type']}):")
            # Show a truncated version
            rec_str = json.dumps(te["rec"], default=str)
            if len(rec_str) > 500:
                rec_str = rec_str[:500] + "..."
            print(f"    {rec_str}")
            print()
    else:
        print("No thinking-related events found.")

    # Look for any token/usage-related keys we might be missing
    print("--- Token-Related Keys ---")
    token_keys = [k for k in all_keys_seen if any(
        word in k.lower() for word in ["token", "usage", "cache", "think", "reason"]
    )]
    for k in sorted(token_keys):
        print(f"  {k}")


def _collect_keys(obj, prefix, keys_set):
    """Recursively collect all key paths from a nested dict/list."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else k
            keys_set.add(path)
            _collect_keys(v, path, keys_set)
    elif isinstance(obj, list):
        for i, item in enumerate(obj[:3]):  # Sample first 3
            _collect_keys(item, f"{prefix}[]", keys_set)


# ---------------------------------------------------------------------------
# Bonus: scan existing sessions for patterns
# ---------------------------------------------------------------------------

def cmd_scan(project_filter=None, max_sessions=20):
    """Scan existing sessions to find patterns in token accounting.

    Usage:
        python3 scripts/token_audit.py scan
        python3 scripts/token_audit.py scan token-char
        python3 scripts/token_audit.py scan --max 50
    """
    print("=" * 72)
    print("TOKEN AUDIT — Scan Existing Sessions")
    print("=" * 72)
    print()

    pattern = str(PROJECTS_DIR / "*" / "*.jsonl")
    all_files = glob.glob(pattern)

    if project_filter:
        all_files = [f for f in all_files if project_filter in f]

    print(f"Found {len(all_files)} session files" +
          (f" matching '{project_filter}'" if project_filter else ""))
    print(f"Analyzing up to {max_sessions}...")
    print()

    # Aggregate stats
    thinking_sessions = []
    all_ratios = []

    for filepath in all_files[:max_sessions]:
        session_id = Path(filepath).stem
        turns = parse_session(filepath)
        if not turns:
            continue

        for t in turns:
            uf = t["usage_flat"]
            if t["has_thinking"]:
                thinking_sessions.append({
                    "session": session_id[:12],
                    "turn": t["turn_number"],
                    "output_tokens": uf["output_tokens"],
                    "visible_chars": t["visible_text_len"],
                    "thinking_chars": t["thinking_text_len"],
                    "cache_create": uf["cache_creation_input_tokens"],
                })

            if t["visible_text_len"] > 10 and uf["output_tokens"] > 0:
                ratio = t["visible_text_len"] / uf["output_tokens"]
                all_ratios.append({
                    "session": session_id[:12],
                    "turn": t["turn_number"],
                    "ratio": ratio,
                    "has_thinking": t["has_thinking"],
                    "output_tokens": uf["output_tokens"],
                    "visible_chars": t["visible_text_len"],
                })

    # Thinking turns analysis
    if thinking_sessions:
        print(f"--- Turns with Thinking Blocks ({len(thinking_sessions)}) ---")
        headers = ["Session", "Turn", "OutTok", "VisCh", "ThinkCh", "CacheCr"]
        rows = [
            [
                s["session"], s["turn"],
                fmt_num(s["output_tokens"]),
                fmt_num(s["visible_chars"]),
                fmt_num(s["thinking_chars"]),
                fmt_num(s["cache_create"]),
            ]
            for s in thinking_sessions
        ]
        print_table(headers, rows)
        print()

    # Chars/token ratio analysis
    if all_ratios:
        ratios_with_thinking = [r for r in all_ratios if r["has_thinking"]]
        ratios_without = [r for r in all_ratios if not r["has_thinking"]]

        print("--- Chars/Token Ratio (visible_chars / output_tokens) ---")
        print()

        if ratios_without:
            vals = [r["ratio"] for r in ratios_without]
            vals.sort()
            median = vals[len(vals) // 2]
            print(f"  Without thinking: n={len(vals)}, "
                  f"median={median:.1f}, "
                  f"min={vals[0]:.1f}, max={vals[-1]:.1f}")

        if ratios_with_thinking:
            vals = [r["ratio"] for r in ratios_with_thinking]
            vals.sort()
            median = vals[len(vals) // 2]
            print(f"  With thinking:    n={len(vals)}, "
                  f"median={median:.1f}, "
                  f"min={vals[0]:.1f}, max={vals[-1]:.1f}")

        print()
        print("  If thinking tokens are in output_tokens, the 'with thinking' ratio")
        print("  should be LOWER (more tokens per visible char).")
        print("  If thinking tokens are NOT in output_tokens, ratios should be similar.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "generate":
        cmd_generate()

    elif cmd == "analyze-session":
        if len(sys.argv) < 3:
            print("Usage: token_audit.py analyze-session <session_id>", file=sys.stderr)
            sys.exit(1)
        cmd_analyze_session(sys.argv[2])

    elif cmd == "analyze":
        if len(sys.argv) < 4:
            print("Usage: token_audit.py analyze <sid_A> <sid_B> <sid_C>", file=sys.stderr)
            sys.exit(1)
        cmd_analyze(sys.argv[2:])

    elif cmd == "analyze-multiturn":
        if len(sys.argv) < 3:
            print("Usage: token_audit.py analyze-multiturn <session_id>", file=sys.stderr)
            sys.exit(1)
        cmd_analyze_multiturn(sys.argv[2])

    elif cmd == "analyze-stream":
        if len(sys.argv) < 3:
            print("Usage: token_audit.py analyze-stream <capture_file>", file=sys.stderr)
            sys.exit(1)
        cmd_analyze_stream(sys.argv[2])

    elif cmd == "scan":
        project_filter = None
        max_sessions = 20
        args = sys.argv[2:]
        for i, a in enumerate(args):
            if a == "--max" and i + 1 < len(args):
                max_sessions = int(args[i + 1])
            elif not a.startswith("--"):
                project_filter = a
        cmd_scan(project_filter, max_sessions)

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
