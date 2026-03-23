"""Cowork (Claude Desktop) session parser."""

import glob
import json
import os
import sys

from ._common import (
    get_hostname,
    is_genuine_user_turn,
    model_family,
    parse_timestamp,
    response_identity,
    safe_int,
    usage_int,
)


def _assistant_turn_template(source, machine, project_name, session_id,
                             response_key, is_subagent, subagent_id):
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


def _parse_project(data_dir, machine, skip_first_n, project_name):
    """Parse a single Cowork project directory."""
    source = "cowork"
    if not project_name:
        project_name = os.path.basename(data_dir)

    json_pattern = os.path.join(data_dir, "local_*.json")
    json_files = glob.glob(json_pattern)

    if not json_files:
        return [], []

    raw_sessions = []

    for jf in sorted(json_files):
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
                for raw_line in fh:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    rec_type = rec.get("type")
                    ts_str = (
                        rec.get("_audit_timestamp")
                        or rec.get("message", {}).get("_audit_timestamp")
                    )
                    ts_iso = parse_timestamp(ts_str)

                    if rec_type == "user":
                        content = rec.get("message", {}).get("content", "")
                        if is_genuine_user_turn(content):
                            turns_user += 1
                        continue

                    if rec_type == "assistant":
                        msg = rec.get("message", {})
                        usage = msg.get("usage", {})
                        if not usage:
                            continue

                        response_key = response_identity(
                            rec,
                            ["message.id", "uuid"],
                            fallback=f"assistant:{fallback_idx}",
                        )
                        if response_key not in response_map:
                            is_subagent = bool(rec.get("parent_tool_use_id"))
                            turn = _assistant_turn_template(
                                source,
                                machine,
                                project_name,
                                session_id,
                                response_key,
                                is_subagent,
                                rec.get("parent_tool_use_id"),
                            )
                            response_map[response_key] = turn
                            response_order.append(turn)
                            fallback_idx += 1

                        turn = response_map[response_key]
                        _update_assistant_turn(
                            turn,
                            ts_iso,
                            msg.get("model", ""),
                            usage,
                        )
                        continue

                    if rec_type == "result":
                        has_result_records = True
                        result_output_tokens += _result_output_tokens(rec)
                        cost = rec.get("total_cost_usd")
                        if cost is not None:
                            result_cost_usd += cost
        except OSError:
            continue

        for idx, turn in enumerate(response_order, 1):
            turn["turn_number"] = idx
            turn.pop("_response_key", None)

        created_at_ms = meta.get("createdAt")
        last_activity_ms = meta.get("lastActivityAt")
        duration_min = None
        if created_at_ms and last_activity_ms:
            duration_min = round((last_activity_ms - created_at_ms) / 60_000, 1)

        created_at_iso = None
        if created_at_ms:
            try:
                from datetime import datetime, timezone
                created_at_iso = datetime.fromtimestamp(
                    created_at_ms / 1000, tz=timezone.utc
                ).isoformat()
            except (OSError, ValueError, OverflowError):
                pass

        model_counts = {}
        for turn in response_order:
            model = turn["model"]
            if model and model != "<synthetic>":
                model_counts[model] = model_counts.get(model, 0) + 1
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

        raw_sessions.append({
            "created_at_ms": created_at_ms,
            "session_id": session_id,
            "turns": response_order,
            "session_dict": {
                "source": source,
                "machine": machine,
                "project": project_name,
                "session_id": session_id,
                "title": meta.get("title") or "(untitled)",
                "model": primary_model,
                "created_at": created_at_iso,
                "duration_min": duration_min,
                "turns_user": turns_user,
                "turns_assistant": len(response_order),
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
            },
        })

    raw_sessions.sort(key=lambda s: s.get("created_at_ms") or 0)
    if skip_first_n > 0 and len(raw_sessions) > skip_first_n:
        raw_sessions = raw_sessions[skip_first_n:]

    turns = []
    sessions = []
    for session in raw_sessions:
        turns.extend(session["turns"])
        sessions.append(session["session_dict"])

    print(f"  cowork: {len(sessions)} sessions, {len(turns)} turns", file=sys.stderr)
    return turns, sessions


def extract_cowork(data_dir, skip_first_n=0, machine="", project_name=None):
    """Extract turns and sessions from Cowork audit logs."""
    if not machine:
        machine = get_hostname()

    json_pattern = os.path.join(data_dir, "local_*.json")
    json_files = glob.glob(json_pattern)

    if json_files:
        return _parse_project(data_dir, machine, skip_first_n, project_name)

    all_turns = []
    all_sessions = []
    for org_dir in sorted(glob.glob(os.path.join(data_dir, "*"))):
        if not os.path.isdir(org_dir):
            continue
        for proj_dir in sorted(glob.glob(os.path.join(org_dir, "*"))):
            if not os.path.isdir(proj_dir):
                continue
            pname = project_name or os.path.basename(proj_dir)
            turns, sessions = _parse_project(proj_dir, machine, skip_first_n, pname)
            all_turns.extend(turns)
            all_sessions.extend(sessions)

    return all_turns, all_sessions
