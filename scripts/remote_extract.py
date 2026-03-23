#!/usr/bin/env python3
"""
Self-contained token usage extractor — stdlib only, no install needed.

Designed to be piped via SSH:
    ssh user@host python3 < scripts/remote_extract.py > host.json

Environment variables:
    MACHINE_NAME        Override hostname (default: platform.node())
    TC_SOURCE           cowork, claude_code, codex, or all (default: all)
    TC_COWORK_DIR       Override Cowork data directory
    TC_CLAUDE_CODE_DIR  Override Claude Code projects directory
    TC_CODEX_DIR        Override Codex sessions directory
"""

import json
import glob
import os
import platform
import sys
from datetime import datetime, timezone

VERSION = "0.1.0"


def parse_timestamp(ts_str):
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, AttributeError):
        return None


def model_family(model_name):
    if not model_name:
        return "unknown"
    m = model_name.lower()
    if "haiku" in m:
        return "haiku"
    if "sonnet" in m:
        return "sonnet"
    if "opus" in m:
        return "opus"
    if "gpt" in m:
        return "gpt"
    return "unknown"


def is_genuine_user_turn(content):
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        return not any(
            isinstance(c, dict) and c.get("type") == "tool_result"
            for c in content
        )
    return False


def duration_minutes(first_ts, last_ts):
    if not first_ts or not last_ts:
        return None
    try:
        dt_first = datetime.fromisoformat(first_ts)
        dt_last = datetime.fromisoformat(last_ts)
        delta = (dt_last - dt_first).total_seconds() / 60
        if delta > 0:
            return round(delta, 1)
    except (ValueError, TypeError):
        return None
    return None


def nested_get(obj, path, default=None):
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part, default)
        else:
            return default
    return cur


def safe_int(value):
    if value in (None, ""):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def usage_int(usage, key):
    if not isinstance(usage, dict):
        return 0
    return safe_int(usage.get(key, 0))


def response_identity(rec, paths, fallback=None):
    for path in paths:
        value = nested_get(rec, path)
        if value not in (None, ""):
            return str(value)
    return fallback


def default_data_dir(source):
    system = platform.system()
    if source == "cowork":
        if system == "Darwin":
            return os.path.expanduser(
                "~/Library/Application Support/Claude/local-agent-mode-sessions"
            )
        elif system == "Linux":
            return os.path.expanduser(
                "~/.config/Claude/local-agent-mode-sessions"
            )
        elif system == "Windows":
            appdata = os.environ.get("APPDATA", "")
            if appdata:
                return os.path.join(appdata, "Claude", "local-agent-mode-sessions")
    elif source == "claude_code":
        if system in ("Darwin", "Linux", "Windows"):
            return os.path.expanduser("~/.claude/projects")
    elif source == "codex":
        if system in ("Darwin", "Linux", "Windows"):
            return os.path.expanduser("~/.codex/sessions")
    return None


def extract_cowork(data_dir, machine):
    turns = []
    sessions = []

    json_files = glob.glob(os.path.join(data_dir, "local_*.json"))
    if not json_files:
        # Try auto-discovering org/project subdirs
        for org_dir in sorted(glob.glob(os.path.join(data_dir, "*"))):
            if not os.path.isdir(org_dir):
                continue
            for proj_dir in sorted(glob.glob(os.path.join(org_dir, "*"))):
                if not os.path.isdir(proj_dir):
                    continue
                t, s = _parse_cowork_project(proj_dir, machine, os.path.basename(proj_dir))
                turns.extend(t)
                sessions.extend(s)
        return turns, sessions

    return _parse_cowork_project(data_dir, machine, os.path.basename(data_dir))


def _parse_cowork_project(data_dir, machine, project_name):
    turns = []
    sessions = []
    source = "cowork"

    for jf in sorted(glob.glob(os.path.join(data_dir, "local_*.json"))):
        try:
            with open(jf, "r", encoding="utf-8") as fh:
                meta = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue

        session_id = meta.get("sessionId", "").replace("local_", "")
        audit_path = os.path.join(data_dir, f"local_{session_id}", "audit.jsonl")

        if not os.path.isfile(audit_path):
            continue

        turns_user = 0
        response_order = []
        response_map = {}
        fallback_idx = 0
        result_output_tokens = 0
        result_cost_usd = 0.0
        has_result_records = False

        try:
            with open(audit_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    rec_type = rec.get("type")
                    ts_str = rec.get("_audit_timestamp") or rec.get("message", {}).get("_audit_timestamp")
                    ts_iso = parse_timestamp(ts_str)

                    if rec_type == "user":
                        content = rec.get("message", {}).get("content", "")
                        if is_genuine_user_turn(content):
                            turns_user += 1

                    elif rec_type == "assistant":
                        msg = rec.get("message", {})
                        usage = msg.get("usage", {})
                        if not usage:
                            continue
                        mdl = msg.get("model", "")
                        response_key = response_identity(
                            rec,
                            ["message.id", "uuid"],
                            fallback=f"assistant:{fallback_idx}",
                        )
                        if response_key not in response_map:
                            is_subagent = bool(rec.get("parent_tool_use_id"))
                            response_map[response_key] = {
                                "source": source,
                                "machine": machine,
                                "project": project_name,
                                "session_id": session_id,
                                "turn_number": 0,
                                "timestamp": None,
                                "model": "",
                                "model_family": "unknown",
                                "input_tokens": 0,
                                "output_tokens": 0,
                                "output_tokens_reliable": False,
                                "output_tokens_source": "assistant_snapshot",
                                "cache_read_tokens": 0,
                                "cache_create_tokens": 0,
                                "reasoning_output_tokens": 0,
                                "total_tokens": 0,
                                "is_subagent": is_subagent,
                                "subagent_id": rec.get("parent_tool_use_id"),
                            }
                            response_order.append(response_map[response_key])
                            fallback_idx += 1

                        turn = response_map[response_key]
                        if ts_iso:
                            turn["timestamp"] = ts_iso
                        if mdl:
                            if turn["model"] in ("", "<synthetic>") or mdl != "<synthetic>":
                                turn["model"] = mdl
                                turn["model_family"] = model_family(mdl)
                        turn["input_tokens"] = max(
                            turn["input_tokens"], usage_int(usage, "input_tokens")
                        )
                        turn["output_tokens"] = max(
                            turn["output_tokens"], usage_int(usage, "output_tokens")
                        )
                        turn["cache_read_tokens"] = max(
                            turn["cache_read_tokens"],
                            usage_int(usage, "cache_read_input_tokens"),
                        )
                        turn["cache_create_tokens"] = max(
                            turn["cache_create_tokens"],
                            usage_int(usage, "cache_creation_input_tokens"),
                        )
                        turn["total_tokens"] = (
                            turn["input_tokens"]
                            + turn["output_tokens"]
                            + turn["cache_read_tokens"]
                            + turn["cache_create_tokens"]
                        )

                    elif rec_type == "result":
                        has_result_records = True
                        model_usage = rec.get("modelUsage", {})
                        if isinstance(model_usage, dict) and model_usage:
                            for usage in model_usage.values():
                                if isinstance(usage, dict):
                                    result_output_tokens += safe_int(
                                        usage.get("outputTokens", 0)
                                    )
                        else:
                            result_output_tokens += usage_int(
                                rec.get("usage", {}),
                                "output_tokens",
                            )
                        cost = rec.get("total_cost_usd")
                        if cost is not None:
                            result_cost_usd += cost
        except OSError:
            continue

        for idx, turn in enumerate(response_order, 1):
            turn["turn_number"] = idx

        created_at_ms = meta.get("createdAt")
        last_activity_ms = meta.get("lastActivityAt")
        duration_min = None
        if created_at_ms and last_activity_ms:
            duration_min = round((last_activity_ms - created_at_ms) / 60_000, 1)

        created_at_iso = None
        if created_at_ms:
            try:
                created_at_iso = datetime.fromtimestamp(
                    created_at_ms / 1000, tz=timezone.utc
                ).isoformat()
            except (OSError, ValueError, OverflowError):
                pass

        model_counts = {}
        for t in response_order:
            m = t["model"]
            if m and m != "<synthetic>":
                model_counts[m] = model_counts.get(m, 0) + 1
        primary_model = max(model_counts, key=model_counts.get) if model_counts else meta.get("model", "")

        total_input_tokens = sum(t["input_tokens"] for t in response_order)
        total_cache_read_tokens = sum(t["cache_read_tokens"] for t in response_order)
        total_cache_create_tokens = sum(t["cache_create_tokens"] for t in response_order)
        summed_output_tokens = sum(t["output_tokens"] for t in response_order)
        if has_result_records:
            total_output_tokens = result_output_tokens
            total_output_tokens_reliable = True
            total_output_tokens_source = "result"
            total_cost_usd = round(result_cost_usd, 6) if result_cost_usd else None
        else:
            total_output_tokens = summed_output_tokens
            total_output_tokens_reliable = False
            total_output_tokens_source = "assistant_snapshot"
            total_cost_usd = None

        turns.extend(response_order)
        sessions.append({
            "source": source, "machine": machine,
            "project": project_name, "session_id": session_id,
            "title": meta.get("title") or "(untitled)",
            "model": primary_model,
            "created_at": created_at_iso, "duration_min": duration_min,
            "turns_user": turns_user, "turns_assistant": len(response_order),
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_output_tokens_reliable": total_output_tokens_reliable,
            "total_output_tokens_source": total_output_tokens_source,
            "total_cache_read_tokens": total_cache_read_tokens,
            "total_cache_create_tokens": total_cache_create_tokens,
            "total_reasoning_output_tokens": 0,
            "total_tokens": (
                total_input_tokens
                + total_output_tokens
                + total_cache_read_tokens
                + total_cache_create_tokens
            ),
            "total_cost_usd": total_cost_usd,
            "subagent_turns": sum(1 for t in response_order if t["is_subagent"]),
        })

    return turns, sessions


def extract_claude_code(projects_dir, machine):
    turns = []
    sessions = []
    source = "claude_code"

    if not os.path.isdir(projects_dir):
        return turns, sessions

    for proj_dir in sorted(glob.glob(os.path.join(projects_dir, "*"))):
        if not os.path.isdir(proj_dir):
            continue

        dirname = os.path.basename(proj_dir)
        # Windows-encoded path: drive letter followed by -- (e.g. C--Users-foo)
        if len(dirname) >= 3 and dirname[0].isalpha() and dirname[1:3] == "--":
            project_name = dirname[0] + ":\\" + dirname[3:].replace("-", "\\")
        elif dirname.startswith("-"):
            project_name = "/" + dirname[1:].replace("-", "/")
        else:
            project_name = dirname

        for jf in sorted(glob.glob(os.path.join(proj_dir, "*.jsonl"))):
            session_id = os.path.splitext(os.path.basename(jf))[0]

            turns_user = 0
            response_order = []
            response_map = {}
            fallback_idx = 0
            stream_output = {}
            active_stream_key = None
            last_assistant_key = None
            result_output_tokens = 0
            has_result_records = False
            first_user_text = None
            session_model = ""
            first_ts = None
            last_ts = None

            try:
                with open(jf, "r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        rec_type = rec.get("type", "")
                        if rec_type in ("file-history-snapshot", "system"):
                            continue

                        ts_str = rec.get("timestamp", "")
                        ts_iso = parse_timestamp(ts_str)
                        if ts_iso:
                            if first_ts is None:
                                first_ts = ts_iso
                            last_ts = ts_iso

                        if rec_type == "user":
                            msg = rec.get("message", {})
                            content = msg.get("content", "")
                            if is_genuine_user_turn(content):
                                turns_user += 1
                                if first_user_text is None and isinstance(content, str):
                                    first_user_text = content.strip()

                        elif rec_type == "assistant":
                            msg = rec.get("message", {})
                            usage = msg.get("usage", {})
                            if not usage:
                                continue

                            mdl = msg.get("model", "")
                            response_key = response_identity(
                                rec,
                                ["requestId", "message.id", "uuid"],
                                fallback=f"assistant:{fallback_idx}",
                            )
                            if response_key not in response_map:
                                response_map[response_key] = {
                                    "source": source,
                                    "machine": machine,
                                    "project": project_name,
                                    "session_id": session_id,
                                    "turn_number": 0,
                                    "timestamp": None,
                                    "model": "",
                                    "model_family": "unknown",
                                    "input_tokens": 0,
                                    "output_tokens": 0,
                                    "output_tokens_reliable": False,
                                    "output_tokens_source": "assistant_snapshot",
                                    "cache_read_tokens": 0,
                                    "cache_create_tokens": 0,
                                    "reasoning_output_tokens": 0,
                                    "total_tokens": 0,
                                    "is_subagent": False,
                                    "subagent_id": None,
                                    "_response_key": response_key,
                                }
                                response_order.append(response_map[response_key])
                                fallback_idx += 1

                            turn = response_map[response_key]
                            if ts_iso:
                                turn["timestamp"] = ts_iso
                            if mdl:
                                if turn["model"] in ("", "<synthetic>") or mdl != "<synthetic>":
                                    turn["model"] = mdl
                                    turn["model_family"] = model_family(mdl)
                            turn["input_tokens"] = max(
                                turn["input_tokens"], usage_int(usage, "input_tokens")
                            )
                            turn["output_tokens"] = max(
                                turn["output_tokens"], usage_int(usage, "output_tokens")
                            )
                            turn["cache_read_tokens"] = max(
                                turn["cache_read_tokens"],
                                usage_int(usage, "cache_read_input_tokens"),
                            )
                            turn["cache_create_tokens"] = max(
                                turn["cache_create_tokens"],
                                usage_int(usage, "cache_creation_input_tokens"),
                            )
                            turn["total_tokens"] = (
                                turn["input_tokens"]
                                + turn["output_tokens"]
                                + turn["cache_read_tokens"]
                                + turn["cache_create_tokens"]
                            )
                            last_assistant_key = response_key
                            if active_stream_key is None:
                                active_stream_key = response_key
                            if turn["model"] and turn["model"] != "<synthetic>" and not session_model:
                                session_model = turn["model"]

                        elif rec_type == "stream_event":
                            event = rec.get("event", {})
                            evt_type = event.get("type")
                            if evt_type == "message_start":
                                message = event.get("message", {})
                                active_stream_key = response_identity(
                                    {"message": message, "uuid": rec.get("uuid")},
                                    ["message.id", "uuid"],
                                    fallback=last_assistant_key,
                                )
                                mdl = message.get("model", "")
                                if mdl and mdl != "<synthetic>" and not session_model:
                                    session_model = mdl
                            elif evt_type == "message_delta":
                                usage = event.get("usage", {})
                                stream_key = active_stream_key or last_assistant_key
                                if stream_key and usage:
                                    stream_output[stream_key] = max(
                                        stream_output.get(stream_key, 0),
                                        usage_int(usage, "output_tokens"),
                                    )
                            elif evt_type == "message_stop":
                                active_stream_key = None

                        elif rec_type == "result":
                            has_result_records = True
                            model_usage = rec.get("modelUsage", {})
                            if isinstance(model_usage, dict) and model_usage:
                                for usage in model_usage.values():
                                    if isinstance(usage, dict):
                                        result_output_tokens += safe_int(
                                            usage.get("outputTokens", 0)
                                        )
                            else:
                                result_output_tokens += usage_int(
                                    rec.get("usage", {}),
                                    "output_tokens",
                                )
            except OSError:
                continue

            for turn in response_order:
                msg_key = turn.get("_response_key")
                if msg_key in stream_output:
                    turn["output_tokens"] = stream_output[msg_key]
                    turn["output_tokens_reliable"] = True
                    turn["output_tokens_source"] = "message_delta"
                    turn["total_tokens"] = (
                        turn["input_tokens"]
                        + turn["output_tokens"]
                        + turn["cache_read_tokens"]
                        + turn["cache_create_tokens"]
                    )

            session_turns = list(response_order)

            # Parse subagent files for this session
            subagent_turns_count = 0
            subagent_pattern = os.path.join(proj_dir, session_id, "subagents", "*.jsonl")
            for sa_file in sorted(glob.glob(subagent_pattern)):
                sa_basename = os.path.basename(sa_file)
                sa_name = os.path.splitext(sa_basename)[0]
                if sa_name.startswith("agent-"):
                    agent_id = sa_name[len("agent-"):]
                else:
                    agent_id = sa_name

                try:
                    with open(sa_file, "r", encoding="utf-8") as fh:
                        sa_response_order = []
                        sa_response_map = {}
                        sa_fallback_idx = 0
                        sa_stream_output = {}
                        sa_active_stream_key = None
                        sa_last_assistant_key = None
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

                            if rec_type == "assistant":
                                msg = rec.get("message", {})
                                usage = msg.get("usage", {})
                                if not usage:
                                    continue

                                response_key = response_identity(
                                    rec,
                                    ["requestId", "message.id", "uuid"],
                                    fallback=f"assistant:{sa_fallback_idx}",
                                )
                                if response_key not in sa_response_map:
                                    sa_response_map[response_key] = {
                                        "source": source,
                                        "machine": machine,
                                        "project": project_name,
                                        "session_id": session_id,
                                        "turn_number": 0,
                                        "timestamp": None,
                                        "model": "",
                                        "model_family": "unknown",
                                        "input_tokens": 0,
                                        "output_tokens": 0,
                                        "output_tokens_reliable": False,
                                        "output_tokens_source": "assistant_snapshot",
                                        "cache_read_tokens": 0,
                                        "cache_create_tokens": 0,
                                        "reasoning_output_tokens": 0,
                                        "total_tokens": 0,
                                        "is_subagent": True,
                                        "subagent_id": agent_id,
                                        "_response_key": response_key,
                                    }
                                    sa_response_order.append(sa_response_map[response_key])
                                    sa_fallback_idx += 1

                                turn = sa_response_map[response_key]
                                if ts_iso:
                                    turn["timestamp"] = ts_iso
                                mdl = msg.get("model", "")
                                if mdl:
                                    if turn["model"] in ("", "<synthetic>") or mdl != "<synthetic>":
                                        turn["model"] = mdl
                                        turn["model_family"] = model_family(mdl)
                                turn["input_tokens"] = max(
                                    turn["input_tokens"], usage_int(usage, "input_tokens")
                                )
                                turn["output_tokens"] = max(
                                    turn["output_tokens"], usage_int(usage, "output_tokens")
                                )
                                turn["cache_read_tokens"] = max(
                                    turn["cache_read_tokens"],
                                    usage_int(usage, "cache_read_input_tokens"),
                                )
                                turn["cache_create_tokens"] = max(
                                    turn["cache_create_tokens"],
                                    usage_int(usage, "cache_creation_input_tokens"),
                                )
                                turn["total_tokens"] = (
                                    turn["input_tokens"]
                                    + turn["output_tokens"]
                                    + turn["cache_read_tokens"]
                                    + turn["cache_create_tokens"]
                                )
                                sa_last_assistant_key = response_key
                                if sa_active_stream_key is None:
                                    sa_active_stream_key = response_key

                            elif rec_type == "stream_event":
                                event = rec.get("event", {})
                                evt_type = event.get("type")
                                if evt_type == "message_start":
                                    message = event.get("message", {})
                                    sa_active_stream_key = response_identity(
                                        {"message": message, "uuid": rec.get("uuid")},
                                        ["message.id", "uuid"],
                                        fallback=sa_last_assistant_key,
                                    )
                                elif evt_type == "message_delta":
                                    usage = event.get("usage", {})
                                    stream_key = sa_active_stream_key or sa_last_assistant_key
                                    if stream_key and usage:
                                        sa_stream_output[stream_key] = max(
                                            sa_stream_output.get(stream_key, 0),
                                            usage_int(usage, "output_tokens"),
                                        )
                                elif evt_type == "message_stop":
                                    sa_active_stream_key = None
                except OSError:
                    continue

                for turn in sa_response_order:
                    msg_key = turn.get("_response_key")
                    if msg_key in sa_stream_output:
                        turn["output_tokens"] = sa_stream_output[msg_key]
                        turn["output_tokens_reliable"] = True
                        turn["output_tokens_source"] = "message_delta"
                        turn["total_tokens"] = (
                            turn["input_tokens"]
                            + turn["output_tokens"]
                            + turn["cache_read_tokens"]
                            + turn["cache_create_tokens"]
                        )

                session_turns.extend(sa_response_order)
                subagent_turns_count += len(sa_response_order)

            for idx, turn in enumerate(session_turns, 1):
                turn["turn_number"] = idx
                turn.pop("_response_key", None)

            if not session_turns:
                continue

            if not session_model:
                for turn in session_turns:
                    mdl = turn.get("model", "")
                    if mdl and mdl != "<synthetic>":
                        session_model = mdl
                        break

            title = "(untitled)"
            if first_user_text:
                title = first_user_text[:80]
                if len(first_user_text) > 80:
                    title += "..."

            duration_min = duration_minutes(first_ts, last_ts)

            total_input_tokens = sum(t["input_tokens"] for t in session_turns)
            total_cache_read_tokens = sum(t["cache_read_tokens"] for t in session_turns)
            total_cache_create_tokens = sum(t["cache_create_tokens"] for t in session_turns)
            summed_output_tokens = sum(t["output_tokens"] for t in session_turns)
            all_turn_outputs_reliable = all(
                t.get("output_tokens_reliable", False) for t in session_turns
            )
            if has_result_records:
                total_output_tokens = result_output_tokens
                total_output_tokens_reliable = True
                total_output_tokens_source = "result"
            else:
                total_output_tokens = summed_output_tokens
                total_output_tokens_reliable = all_turn_outputs_reliable
                total_output_tokens_source = (
                    "message_delta" if all_turn_outputs_reliable else "assistant_snapshot"
                )

            turns.extend(session_turns)
            sessions.append({
                "source": source, "machine": machine,
                "project": project_name, "session_id": session_id,
                "title": title, "model": session_model,
                "created_at": first_ts, "duration_min": duration_min,
                "turns_user": turns_user, "turns_assistant": len(session_turns),
                "total_input_tokens": total_input_tokens,
                "total_output_tokens": total_output_tokens,
                "total_output_tokens_reliable": total_output_tokens_reliable,
                "total_output_tokens_source": total_output_tokens_source,
                "total_cache_read_tokens": total_cache_read_tokens,
                "total_cache_create_tokens": total_cache_create_tokens,
                "total_reasoning_output_tokens": 0,
                "total_tokens": (
                    total_input_tokens
                    + total_output_tokens
                    + total_cache_read_tokens
                    + total_cache_create_tokens
                ),
                "total_cost_usd": None,
                "subagent_turns": subagent_turns_count,
            })

    return turns, sessions


def extract_codex(sessions_dir, machine):
    """Extract turns and sessions from Codex JSONL session files."""
    turns = []
    sessions = []
    source = "codex"

    if not os.path.isdir(sessions_dir):
        return turns, sessions

    pattern = os.path.join(sessions_dir, "**", "rollout-*.jsonl")
    for jf in sorted(glob.glob(pattern, recursive=True)):
        session_id = ""
        session_cwd = ""
        first_user_text = None
        first_ts = None
        last_ts = None

        prev_total = {"input_tokens": 0, "cached_input_tokens": 0,
                      "output_tokens": 0, "reasoning_output_tokens": 0}
        latest_total = None
        current_model = ""
        current_turn_start_ts = None
        current_turn_total_at_start = None

        session_turns = []
        turn_number = 0

        try:
            with open(jf, "r", encoding="utf-8") as fh:
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

                    if rec_type == "session_meta":
                        payload = rec.get("payload", {})
                        session_id = payload.get("id", "")
                        session_cwd = payload.get("cwd", "")

                    elif rec_type == "turn_context":
                        payload = rec.get("payload", {})
                        mdl = payload.get("model", "")
                        if mdl:
                            current_model = mdl

                    elif rec_type == "event_msg":
                        payload = rec.get("payload", {})
                        evt_type = payload.get("type", "")

                        if evt_type == "task_started":
                            current_turn_start_ts = ts_iso
                            if latest_total is not None:
                                current_turn_total_at_start = dict(latest_total)
                            else:
                                current_turn_total_at_start = dict(prev_total)

                        elif evt_type == "user_message":
                            msg_text = payload.get("message", "")
                            if msg_text and isinstance(msg_text, str):
                                if first_user_text is None:
                                    first_user_text = msg_text.strip()

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
                            if current_turn_total_at_start is not None and latest_total is not None:
                                turn_number += 1
                                delta = {
                                    k: latest_total[k] - current_turn_total_at_start.get(k, 0)
                                    for k in latest_total
                                }
                                codex_input = delta["input_tokens"]
                                codex_cached = delta["cached_input_tokens"]
                                our_input = max(0, codex_input - codex_cached)
                                our_output = delta["output_tokens"]
                                our_cache_read = codex_cached
                                our_reasoning = delta["reasoning_output_tokens"]
                                our_total = our_input + our_output + our_cache_read

                                normalized_cwd = session_cwd.rstrip("/\\").replace("\\", "/")
                                project_name = os.path.basename(normalized_cwd) or session_cwd or "(unknown)"
                                session_turns.append({
                                    "source": source, "machine": machine,
                                    "project": project_name, "session_id": session_id,
                                    "turn_number": turn_number, "timestamp": current_turn_start_ts,
                                    "model": current_model, "model_family": model_family(current_model),
                                    "input_tokens": our_input, "output_tokens": our_output,
                                    "output_tokens_reliable": True,
                                    "output_tokens_source": "api_usage",
                                    "cache_read_tokens": our_cache_read, "cache_create_tokens": 0,
                                    "reasoning_output_tokens": our_reasoning,
                                    "total_tokens": our_total,
                                    "is_subagent": False, "subagent_id": None,
                                })
                            current_turn_total_at_start = None
        except OSError:
            continue

        if not session_turns:
            continue

        title = "(untitled)"
        if first_user_text:
            title = first_user_text[:80]
            if len(first_user_text) > 80:
                title += "..."

        duration_min = None
        if first_ts and last_ts:
            try:
                dt_first = datetime.fromisoformat(first_ts)
                dt_last = datetime.fromisoformat(last_ts)
                delta_sec = (dt_last - dt_first).total_seconds() / 60
                if delta_sec > 0:
                    duration_min = round(delta_sec, 1)
            except (ValueError, TypeError):
                pass

        total_input = sum(t["input_tokens"] for t in session_turns)
        total_output = sum(t["output_tokens"] for t in session_turns)
        total_cache_read = sum(t["cache_read_tokens"] for t in session_turns)
        total_reasoning = sum(t["reasoning_output_tokens"] for t in session_turns)

        model_counts = {}
        for t in session_turns:
            m = t["model"]
            if m:
                model_counts[m] = model_counts.get(m, 0) + 1
        primary_model = max(model_counts, key=model_counts.get) if model_counts else ""

        normalized_cwd = session_cwd.rstrip("/\\").replace("\\", "/")
        project_name = os.path.basename(normalized_cwd) or session_cwd or "(unknown)"
        turns.extend(session_turns)
        sessions.append({
            "source": source, "machine": machine,
            "project": project_name, "session_id": session_id,
            "title": title, "model": primary_model,
            "created_at": first_ts, "duration_min": duration_min,
            "turns_user": turn_number, "turns_assistant": turn_number,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_output_tokens_reliable": True,
            "total_output_tokens_source": "api_usage",
            "total_cache_read_tokens": total_cache_read,
            "total_cache_create_tokens": 0,
            "total_reasoning_output_tokens": total_reasoning,
            "total_tokens": total_input + total_output + total_cache_read,
            "total_cost_usd": None,
            "subagent_turns": 0,
        })

    return turns, sessions


def main():
    machine = os.environ.get("MACHINE_NAME", platform.node())
    tc_source = os.environ.get("TC_SOURCE", "all")

    all_turns = []
    all_sessions = []
    sources_used = []

    if tc_source in ("cowork", "all"):
        cowork_dir = os.environ.get("TC_COWORK_DIR") or default_data_dir("cowork")
        if cowork_dir and os.path.isdir(cowork_dir):
            t, s = extract_cowork(cowork_dir, machine)
            all_turns.extend(t)
            all_sessions.extend(s)
            if t or s:
                sources_used.append("cowork")

    if tc_source in ("claude_code", "all"):
        cc_dir = os.environ.get("TC_CLAUDE_CODE_DIR") or default_data_dir("claude_code")
        if cc_dir and os.path.isdir(cc_dir):
            t, s = extract_claude_code(cc_dir, machine)
            all_turns.extend(t)
            all_sessions.extend(s)
            if t or s:
                sources_used.append("claude_code")

    if tc_source in ("codex", "all"):
        codex_dir = os.environ.get("TC_CODEX_DIR") or default_data_dir("codex")
        if codex_dir and os.path.isdir(codex_dir):
            t, s = extract_codex(codex_dir, machine)
            all_turns.extend(t)
            all_sessions.extend(s)
            if t or s:
                sources_used.append("codex")

    envelope = {
        "token_char_version": VERSION,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "machine": machine,
        "sources": sources_used,
        "turns": all_turns,
        "sessions": all_sessions,
    }

    json.dump(envelope, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
