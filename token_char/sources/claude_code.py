"""Claude Code (CLI) session parser."""

import glob
import json
import os
import sys

from ._common import (
    duration_minutes,
    get_hostname,
    is_genuine_user_turn,
    model_family,
    parse_timestamp,
    response_identity,
    safe_int,
    usage_int,
)


def _decode_project_name(dirname):
    """Decode a Claude Code project directory name to a path.
    e.g. '-home-ig88-rpi-deploy' -> '/home/ig88/rpi-deploy'
    On macOS: '-Users-alice-dev-foo' -> '/Users/alice/dev/foo'
    On Windows: 'C--Users-bob-code' -> 'C:\\Users\\bob\\code'
    """
    if len(dirname) >= 3 and dirname[0].isalpha() and dirname[1:3] == "--":
        return dirname[0] + ":\\" + dirname[3:].replace("-", "\\")
    if dirname.startswith("-"):
        return "/" + dirname[1:].replace("-", "/")
    return dirname


def _assistant_turn_template(source, machine, project_name, session_id,
                             is_subagent, subagent_id, response_key):
    return {
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
        "subagent_id": subagent_id,
        "_response_key": response_key,
    }


def _update_assistant_turn(turn, ts_iso, model, usage):
    if ts_iso:
        turn["timestamp"] = ts_iso
    if model:
        if turn["model"] in ("", "<synthetic>") or model != "<synthetic>":
            turn["model"] = model
            turn["model_family"] = model_family(model)

    turn["input_tokens"] = max(turn["input_tokens"], usage_int(usage, "input_tokens"))
    turn["output_tokens"] = max(turn["output_tokens"], usage_int(usage, "output_tokens"))
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


def _result_output_tokens(rec):
    model_usage = rec.get("modelUsage", {})
    if isinstance(model_usage, dict) and model_usage:
        total = 0
        found = False
        for usage in model_usage.values():
            if isinstance(usage, dict):
                total += safe_int(usage.get("outputTokens", 0))
                found = True
        if found:
            return total
    return usage_int(rec.get("usage", {}), "output_tokens")


def _parse_assistant_log(path, source, machine, project_name, session_id,
                         is_subagent=False, subagent_id=None):
    turns_user = 0
    first_user_text = None
    session_model = ""
    first_ts = None
    last_ts = None
    response_order = []
    response_map = {}
    stream_output = {}
    fallback_idx = 0
    active_stream_key = None
    last_assistant_key = None
    result_output_tokens = 0
    has_result_records = False

    with open(path, "r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            rec_type = rec.get("type", "")
            if rec_type in ("file-history-snapshot", "system"):
                continue

            ts_iso = parse_timestamp(rec.get("timestamp", ""))
            if ts_iso:
                if first_ts is None:
                    first_ts = ts_iso
                last_ts = ts_iso

            if rec_type == "user" and not is_subagent:
                content = rec.get("message", {}).get("content", "")
                if is_genuine_user_turn(content):
                    turns_user += 1
                    if first_user_text is None and isinstance(content, str):
                        first_user_text = content.strip()
                continue

            if rec_type == "assistant":
                msg = rec.get("message", {})
                usage = msg.get("usage", {})
                if not usage:
                    continue

                response_key = response_identity(
                    rec,
                    ["requestId", "message.id", "uuid"],
                    fallback=f"assistant:{fallback_idx}",
                )
                if response_key not in response_map:
                    turn = _assistant_turn_template(
                        source,
                        machine,
                        project_name,
                        session_id,
                        is_subagent,
                        subagent_id,
                        response_key,
                    )
                    response_map[response_key] = turn
                    response_order.append(turn)
                    fallback_idx += 1

                turn = response_map[response_key]
                _update_assistant_turn(turn, ts_iso, msg.get("model", ""), usage)
                last_assistant_key = response_key
                if active_stream_key is None:
                    active_stream_key = response_key
                if turn["model"] and turn["model"] != "<synthetic>" and not session_model:
                    session_model = turn["model"]
                continue

            if rec_type == "stream_event":
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
                continue

            if rec_type == "result" and not is_subagent:
                has_result_records = True
                result_output_tokens += _result_output_tokens(rec)

    for turn in response_order:
        response_key = turn["_response_key"]
        if response_key in stream_output:
            turn["output_tokens"] = stream_output[response_key]
            turn["output_tokens_reliable"] = True
            turn["output_tokens_source"] = "message_delta"
            turn["total_tokens"] = (
                turn["input_tokens"]
                + turn["output_tokens"]
                + turn["cache_read_tokens"]
                + turn["cache_create_tokens"]
            )

    for turn in response_order:
        turn.pop("_response_key", None)

    return {
        "turns": response_order,
        "turns_user": turns_user,
        "first_user_text": first_user_text,
        "session_model": session_model,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "result_output_tokens": result_output_tokens,
        "has_result_records": has_result_records,
    }


def extract_claude_code(projects_dir, project_map=None, machine=""):
    """Extract turns and sessions from Claude Code JSONL session files."""
    if not machine:
        machine = get_hostname()
    if project_map is None:
        project_map = {}

    source = "claude_code"
    all_turns = []
    all_sessions = []

    if not os.path.isdir(projects_dir):
        return [], []

    for proj_dir in sorted(glob.glob(os.path.join(projects_dir, "*"))):
        if not os.path.isdir(proj_dir):
            continue

        dirname = os.path.basename(proj_dir)
        project_name = project_map.get(dirname, _decode_project_name(dirname))

        for jf in sorted(glob.glob(os.path.join(proj_dir, "*.jsonl"))):
            session_id = os.path.splitext(os.path.basename(jf))[0]

            try:
                parsed_main = _parse_assistant_log(
                    jf, source, machine, project_name, session_id
                )
            except OSError:
                continue

            session_turns = list(parsed_main["turns"])
            first_user_text = parsed_main["first_user_text"]
            turns_user = parsed_main["turns_user"]
            session_model = parsed_main["session_model"]
            first_ts = parsed_main["first_ts"]
            last_ts = parsed_main["last_ts"]
            result_output_tokens = parsed_main["result_output_tokens"]
            has_result_records = parsed_main["has_result_records"]

            subagent_pattern = os.path.join(proj_dir, session_id, "subagents", "*.jsonl")
            for sa_file in sorted(glob.glob(subagent_pattern)):
                sa_name = os.path.splitext(os.path.basename(sa_file))[0]
                if sa_name.startswith("agent-"):
                    agent_id = sa_name[len("agent-"):]
                else:
                    agent_id = sa_name

                try:
                    parsed_sub = _parse_assistant_log(
                        sa_file,
                        source,
                        machine,
                        project_name,
                        session_id,
                        is_subagent=True,
                        subagent_id=agent_id,
                    )
                except OSError:
                    continue

                session_turns.extend(parsed_sub["turns"])
                if parsed_sub["session_model"] and not session_model:
                    session_model = parsed_sub["session_model"]
                if parsed_sub["first_ts"] and (first_ts is None or parsed_sub["first_ts"] < first_ts):
                    first_ts = parsed_sub["first_ts"]
                if parsed_sub["last_ts"] and (last_ts is None or parsed_sub["last_ts"] > last_ts):
                    last_ts = parsed_sub["last_ts"]

            if not session_turns:
                continue

            for idx, turn in enumerate(session_turns, 1):
                turn["turn_number"] = idx

            if not session_model:
                for turn in session_turns:
                    mdl = turn["model"]
                    if mdl and mdl != "<synthetic>":
                        session_model = mdl
                        break

            total_input_tokens = sum(t["input_tokens"] for t in session_turns)
            total_cache_read_tokens = sum(t["cache_read_tokens"] for t in session_turns)
            total_cache_create_tokens = sum(t["cache_create_tokens"] for t in session_turns)
            sum_turn_output_tokens = sum(t["output_tokens"] for t in session_turns)
            all_turn_outputs_reliable = all(
                t.get("output_tokens_reliable", False) for t in session_turns
            )

            if has_result_records:
                total_output_tokens = result_output_tokens
                total_output_tokens_reliable = True
                total_output_tokens_source = "result"
            else:
                total_output_tokens = sum_turn_output_tokens
                total_output_tokens_reliable = all_turn_outputs_reliable
                total_output_tokens_source = (
                    "message_delta" if all_turn_outputs_reliable else "assistant_snapshot"
                )

            title = "(untitled)"
            if first_user_text:
                title = first_user_text[:80]
                if len(first_user_text) > 80:
                    title += "..."

            all_turns.extend(session_turns)
            all_sessions.append({
                "source": source,
                "machine": machine,
                "project": project_name,
                "session_id": session_id,
                "title": title,
                "model": session_model,
                "created_at": first_ts,
                "duration_min": duration_minutes(first_ts, last_ts),
                "turns_user": turns_user,
                "turns_assistant": len(session_turns),
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
                "subagent_turns": sum(1 for t in session_turns if t["is_subagent"]),
            })

    print(
        f"  claude_code: {len(all_sessions)} sessions, {len(all_turns)} turns",
        file=sys.stderr,
    )
    return all_turns, all_sessions
