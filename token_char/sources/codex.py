"""OpenAI Codex session parser.

Codex stores session logs at ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl.

Token accounting notes:
- OpenAI's input_tokens includes cached tokens; we decompose:
    our input_tokens = codex input_tokens - codex cached_input_tokens
    our cache_read_tokens = codex cached_input_tokens
- OpenAI's output_tokens includes reasoning tokens (standard OpenAI convention):
    our output_tokens = codex output_tokens (unchanged, includes reasoning)
    our reasoning_output_tokens = codex reasoning_output_tokens (subset of output)
- reasoning_output_tokens is informational only — NOT additive in total_tokens.
- cache_create_tokens is always 0 (Codex doesn't expose this).
"""

import json
import os
import glob
import sys

from ._common import parse_timestamp, model_family, get_hostname


def extract_codex(sessions_dir, machine=""):
    """Extract turns and sessions from Codex JSONL session files.

    Args:
        sessions_dir: Path to ~/.codex/sessions (or equivalent)
        machine: Machine name override (default: hostname)

    Returns:
        (turns, sessions) where turns is a list of per-turn dicts
        and sessions is a list of per-session dicts.
    """
    if not machine:
        machine = get_hostname()

    source = "codex"
    all_turns = []
    all_sessions = []

    if not os.path.isdir(sessions_dir):
        return [], []

    pattern = os.path.join(sessions_dir, "**", "rollout-*.jsonl")
    for jf in sorted(glob.glob(pattern, recursive=True)):
        session_turns, session_dict = _parse_session_file(jf, source, machine)
        if session_turns:
            all_turns.extend(session_turns)
            all_sessions.append(session_dict)

    print(
        f"  codex: {len(all_sessions)} sessions, {len(all_turns)} turns",
        file=sys.stderr,
    )
    return all_turns, all_sessions


def _parse_session_file(filepath, source, machine):
    """Parse a single Codex rollout-*.jsonl file into turns and a session dict."""
    session_id = ""
    session_cwd = ""
    cli_version = ""
    first_user_text = None
    first_ts = None
    last_ts = None

    # Accumulate per-turn data as we scan through the file
    # Track the cumulative total_token_usage; per-turn = delta
    prev_total = {"input_tokens": 0, "cached_input_tokens": 0,
                  "output_tokens": 0, "reasoning_output_tokens": 0}
    latest_total = None  # most recent total_token_usage snapshot

    # Per-turn state
    current_turn_id = None
    current_model = ""
    current_turn_user_msg = None
    current_turn_start_ts = None
    current_turn_total_at_start = None  # total_token_usage at turn start

    completed_turns = []  # list of raw turn dicts
    turn_number = 0

    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                rec_type = rec.get("type", "")
                ts_str = rec.get("timestamp", "")
                ts_iso = parse_timestamp(ts_str)

                if ts_iso:
                    if first_ts is None:
                        first_ts = ts_iso
                    last_ts = ts_iso

                # session_meta: session-level metadata
                if rec_type == "session_meta":
                    payload = rec.get("payload", {})
                    session_id = payload.get("id", "")
                    session_cwd = payload.get("cwd", "")
                    cli_version = payload.get("cli_version", "")

                # turn_context: per-turn metadata (model, turn_id)
                elif rec_type == "turn_context":
                    payload = rec.get("payload", {})
                    tid = payload.get("turn_id", "")
                    mdl = payload.get("model", "")
                    if tid and mdl:
                        current_model = mdl
                    if tid:
                        current_turn_id = tid

                # event_msg: various event subtypes
                elif rec_type == "event_msg":
                    payload = rec.get("payload", {})
                    evt_type = payload.get("type", "")

                    if evt_type == "task_started":
                        # New turn begins
                        tid = payload.get("turn_id", "")
                        if tid:
                            current_turn_id = tid
                        current_turn_start_ts = ts_iso
                        current_turn_user_msg = None
                        # Snapshot the cumulative total at turn start
                        if latest_total is not None:
                            current_turn_total_at_start = dict(latest_total)
                        else:
                            current_turn_total_at_start = dict(prev_total)

                    elif evt_type == "user_message":
                        msg_text = payload.get("message", "")
                        if msg_text and isinstance(msg_text, str):
                            if first_user_text is None:
                                first_user_text = msg_text.strip()
                            if current_turn_user_msg is None:
                                current_turn_user_msg = msg_text.strip()

                    elif evt_type == "token_count":
                        info = payload.get("info")
                        if info and isinstance(info, dict):
                            total_usage = info.get("total_token_usage")
                            if total_usage and isinstance(total_usage, dict):
                                latest_total = {
                                    "input_tokens": total_usage.get("input_tokens", 0),
                                    "cached_input_tokens": total_usage.get("cached_input_tokens", 0),
                                    "output_tokens": total_usage.get("output_tokens", 0),
                                    "reasoning_output_tokens": total_usage.get("reasoning_output_tokens", 0),
                                }

                    elif evt_type == "task_complete":
                        # Turn finished — compute delta
                        if current_turn_total_at_start is not None and latest_total is not None:
                            turn_number += 1
                            delta = {
                                k: latest_total[k] - current_turn_total_at_start.get(k, 0)
                                for k in latest_total
                            }

                            # Decompose: OpenAI input includes cached
                            codex_input = delta["input_tokens"]
                            codex_cached = delta["cached_input_tokens"]
                            our_input = codex_input - codex_cached
                            if our_input < 0:
                                our_input = 0
                            our_output = delta["output_tokens"]
                            our_cache_read = codex_cached
                            our_reasoning = delta["reasoning_output_tokens"]
                            our_total = our_input + our_output + our_cache_read

                            completed_turns.append({
                                "source": source,
                                "machine": machine,
                                "project": _project_from_cwd(session_cwd),
                                "session_id": session_id,
                                "turn_number": turn_number,
                                "timestamp": current_turn_start_ts,
                                "model": current_model,
                                "model_family": model_family(current_model),
                                "input_tokens": our_input,
                                "output_tokens": our_output,
                                "cache_read_tokens": our_cache_read,
                                "cache_create_tokens": 0,
                                "reasoning_output_tokens": our_reasoning,
                                "total_tokens": our_total,
                                "is_subagent": False,
                                "subagent_id": None,
                            })

                        # Reset for next turn
                        current_turn_total_at_start = None
                        current_turn_user_msg = None

    except OSError:
        return [], None

    if not completed_turns:
        return [], None

    # Session title from first user message
    title = "(untitled)"
    if first_user_text:
        title = first_user_text[:80]
        if len(first_user_text) > 80:
            title += "..."

    # Duration
    duration_min = None
    if first_ts and last_ts:
        try:
            from datetime import datetime
            dt_first = datetime.fromisoformat(first_ts)
            dt_last = datetime.fromisoformat(last_ts)
            delta_sec = (dt_last - dt_first).total_seconds() / 60
            if delta_sec > 0:
                duration_min = round(delta_sec, 1)
        except (ValueError, TypeError):
            pass

    # Aggregate session-level totals
    total_input = sum(t["input_tokens"] for t in completed_turns)
    total_output = sum(t["output_tokens"] for t in completed_turns)
    total_cache_read = sum(t["cache_read_tokens"] for t in completed_turns)
    total_cache_create = 0
    total_reasoning = sum(t["reasoning_output_tokens"] for t in completed_turns)

    # Primary model (most common)
    model_counts = {}
    for t in completed_turns:
        m = t["model"]
        if m:
            model_counts[m] = model_counts.get(m, 0) + 1
    primary_model = max(model_counts, key=model_counts.get) if model_counts else ""

    session_dict = {
        "source": source,
        "machine": machine,
        "project": _project_from_cwd(session_cwd),
        "session_id": session_id,
        "title": title,
        "model": primary_model,
        "created_at": first_ts,
        "duration_min": duration_min,
        "turns_user": turn_number,  # each task_started/complete = one user turn
        "turns_assistant": turn_number,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_cache_read_tokens": total_cache_read,
        "total_cache_create_tokens": total_cache_create,
        "total_reasoning_output_tokens": total_reasoning,
        "total_tokens": total_input + total_output + total_cache_read + total_cache_create,
        "subagent_turns": 0,
    }

    return completed_turns, session_dict


def _project_from_cwd(cwd):
    """Derive a project name from the session's working directory."""
    if not cwd:
        return "(unknown)"
    return os.path.basename(cwd.rstrip("/\\")) or cwd
