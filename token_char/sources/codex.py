"""OpenAI Codex session parser.

Codex stores session logs at ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl.
Sessions may originate from Codex CLI (codex_cli_rs), VS Code extension
(codex_vscode), or the Codex Desktop app (Codex Desktop).

Turn boundary protocols (in priority order):
1. task_started/task_complete: Used by newer CLI sessions. Each pair = one turn.
2. token_count deltas: Desktop/older sessions emit cumulative token_count
   events after each API call. Each non-zero delta = one turn (finest grain).
3. Session-level totals: If no boundaries at all, emit one synthetic turn.

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


def _decompose_delta(delta):
    """Decompose a raw token delta dict into our schema fields.

    Returns (input, output, cache_read, reasoning, total).
    """
    codex_input = delta["input_tokens"]
    codex_cached = delta["cached_input_tokens"]
    our_input = max(codex_input - codex_cached, 0)
    our_output = delta["output_tokens"]
    our_cache_read = codex_cached
    our_reasoning = delta["reasoning_output_tokens"]
    our_total = our_input + our_output + our_cache_read
    return our_input, our_output, our_cache_read, our_reasoning, our_total


def _parse_session_file(filepath, source, machine):
    """Parse a single Codex rollout-*.jsonl file into turns and a session dict.

    Supports two turn-boundary protocols:
    1. task_started/task_complete (newer CLI): each pair = one turn
    2. user_message fallback (Desktop/older): each user_message starts a turn,
       closed by the next user_message or end-of-file
    """
    session_id = ""
    session_cwd = ""
    session_originator = ""
    first_user_text = None
    first_ts = None
    last_ts = None

    # Cumulative total_token_usage tracking
    zero_total = {"input_tokens": 0, "cached_input_tokens": 0,
                  "output_tokens": 0, "reasoning_output_tokens": 0}
    latest_total = None  # most recent total_token_usage snapshot

    # Per-turn state for task_started/task_complete protocol
    current_model = ""
    current_turn_start_ts = None
    current_turn_total_at_start = None

    # Collect turn data for both protocols
    task_completed_turns = []  # turns from task_started/task_complete
    has_task_events = False

    # For API-call-level fallback: every non-zero token_count delta = a turn
    token_count_snapshots = []  # list of (timestamp, cumulative_total_dict, model)
    user_msg_count = 0

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
                    session_originator = payload.get("originator", "")

                # turn_context: per-turn metadata (model, turn_id)
                elif rec_type == "turn_context":
                    payload = rec.get("payload", {})
                    mdl = payload.get("model", "")
                    if mdl:
                        current_model = mdl

                # event_msg: various event subtypes
                elif rec_type == "event_msg":
                    payload = rec.get("payload", {})
                    evt_type = payload.get("type", "")

                    if evt_type == "task_started":
                        has_task_events = True
                        current_turn_start_ts = ts_iso
                        # Snapshot cumulative total at turn start
                        if latest_total is not None:
                            current_turn_total_at_start = dict(latest_total)
                        else:
                            current_turn_total_at_start = dict(zero_total)

                    elif evt_type == "user_message":
                        msg_text = payload.get("message", "")
                        if msg_text and isinstance(msg_text, str):
                            if first_user_text is None:
                                first_user_text = msg_text.strip()
                        user_msg_count += 1

                    elif evt_type == "token_count":
                        info = payload.get("info")
                        if info and isinstance(info, dict):
                            total_usage = info.get("total_token_usage")
                            if total_usage and isinstance(total_usage, dict):
                                new_total = {
                                    "input_tokens": total_usage.get("input_tokens", 0),
                                    "cached_input_tokens": total_usage.get("cached_input_tokens", 0),
                                    "output_tokens": total_usage.get("output_tokens", 0),
                                    "reasoning_output_tokens": total_usage.get("reasoning_output_tokens", 0),
                                }
                                # Record snapshot for API-call fallback
                                # (only if totals actually changed)
                                prev = latest_total or zero_total
                                if any(new_total[k] != prev.get(k, 0) for k in new_total):
                                    token_count_snapshots.append(
                                        (ts_iso, dict(new_total), current_model)
                                    )
                                latest_total = new_total

                    elif evt_type == "task_complete":
                        has_task_events = True
                        # Turn finished — compute delta
                        if current_turn_total_at_start is not None and latest_total is not None:
                            turn_number += 1
                            delta = {
                                k: latest_total[k] - current_turn_total_at_start.get(k, 0)
                                for k in latest_total
                            }
                            inp, out, cr, reason, total = _decompose_delta(delta)

                            task_completed_turns.append({
                                "source": source,
                                "machine": machine,
                                "project": _project_from_cwd(session_cwd),
                                "session_id": session_id,
                                "turn_number": turn_number,
                                "timestamp": current_turn_start_ts,
                                "model": current_model,
                                "model_family": model_family(current_model),
                                "input_tokens": inp,
                                "output_tokens": out,
                                "cache_read_tokens": cr,
                                "cache_create_tokens": 0,
                                "reasoning_output_tokens": reason,
                                "total_tokens": total,
                                "is_subagent": False,
                                "subagent_id": None,
                            })

                        # Reset for next turn
                        current_turn_total_at_start = None

    except OSError:
        return [], None

    # Choose protocol:
    # 1. task_started/task_complete if available (per-task grain)
    # 2. token_count deltas — each non-zero delta = one API call (finest grain)
    # 3. Single synthetic turn from session-level totals
    if has_task_events and task_completed_turns:
        completed_turns = task_completed_turns
    elif token_count_snapshots:
        completed_turns = _turns_from_token_count_deltas(
            token_count_snapshots, source, machine, session_id, session_cwd,
        )
    elif latest_total and any(v > 0 for v in latest_total.values()):
        completed_turns = _single_turn_from_totals(
            latest_total, first_ts, current_model, source, machine,
            session_id, session_cwd,
        )
    else:
        return [], None

    if not completed_turns:
        return [], None

    return completed_turns, _build_session_dict(
        completed_turns, source, machine, session_id, session_cwd,
        session_originator, first_user_text, first_ts, last_ts,
        user_msg_count,
    )


def _turns_from_token_count_deltas(snapshots, source, machine,
                                    session_id, session_cwd):
    """Create per-API-call turns from cumulative token_count snapshots.

    Each snapshot with a non-zero delta from the previous = one API call = one turn.
    """
    turns = []
    project = _project_from_cwd(session_cwd)
    zero = {"input_tokens": 0, "cached_input_tokens": 0,
            "output_tokens": 0, "reasoning_output_tokens": 0}
    prev = zero

    for ts, cumulative, mdl in snapshots:
        delta = {k: cumulative[k] - prev.get(k, 0) for k in cumulative}
        # Only emit turn if there's actual token activity
        if all(v <= 0 for v in delta.values()):
            prev = cumulative
            continue

        inp, out, cr, reason, total = _decompose_delta(delta)
        turns.append({
            "source": source,
            "machine": machine,
            "project": project,
            "session_id": session_id,
            "turn_number": len(turns) + 1,
            "timestamp": ts,
            "model": mdl,
            "model_family": model_family(mdl),
            "input_tokens": inp,
            "output_tokens": out,
            "cache_read_tokens": cr,
            "cache_create_tokens": 0,
            "reasoning_output_tokens": reason,
            "total_tokens": total,
            "is_subagent": False,
            "subagent_id": None,
        })
        prev = cumulative

    return turns


def _single_turn_from_totals(final_total, first_ts, model, source, machine,
                              session_id, session_cwd):
    """Create a single turn from final cumulative totals (no boundary events)."""
    delta = dict(final_total)  # delta from zero = total
    inp, out, cr, reason, total = _decompose_delta(delta)
    if total <= 0:
        return []
    return [{
        "source": source,
        "machine": machine,
        "project": _project_from_cwd(session_cwd),
        "session_id": session_id,
        "turn_number": 1,
        "timestamp": first_ts,
        "model": model,
        "model_family": model_family(model),
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_tokens": cr,
        "cache_create_tokens": 0,
        "reasoning_output_tokens": reason,
        "total_tokens": total,
        "is_subagent": False,
        "subagent_id": None,
    }]


def _build_session_dict(completed_turns, source, machine, session_id,
                         session_cwd, originator, first_user_text,
                         first_ts, last_ts, user_msg_count):
    """Build a session dict from completed turns."""
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

    n_turns = len(completed_turns)
    return {
        "source": source,
        "machine": machine,
        "project": _project_from_cwd(session_cwd),
        "session_id": session_id,
        "title": title,
        "model": primary_model,
        "created_at": first_ts,
        "duration_min": duration_min,
        "turns_user": max(user_msg_count, n_turns),
        "turns_assistant": n_turns,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_cache_read_tokens": total_cache_read,
        "total_cache_create_tokens": total_cache_create,
        "total_reasoning_output_tokens": total_reasoning,
        "total_tokens": total_input + total_output + total_cache_read + total_cache_create,
        "subagent_turns": 0,
    }


def _project_from_cwd(cwd):
    """Derive a project name from the session's working directory."""
    if not cwd:
        return "(unknown)"
    return os.path.basename(cwd.rstrip("/\\")) or cwd
