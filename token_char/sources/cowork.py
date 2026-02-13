"""Cowork (Claude Desktop) session parser."""

import json
import os
import glob
import sys

from ._common import parse_timestamp, model_family, is_genuine_user_turn, get_hostname


def extract_cowork(data_dir, skip_first_n=0, machine="", project_name=None):
    """Extract turns and sessions from Cowork audit logs.

    Args:
        data_dir: Path to session data. Can be:
            - Root sessions dir (contains <org>/<project>/ subdirs)
            - Direct project dir (contains local_*.json files)
        skip_first_n: Skip the N oldest sessions (chronologically)
        machine: Machine name override (default: hostname)
        project_name: Override project name (default: derived from path)

    Returns:
        (turns, sessions) where turns is a list of per-turn dicts
        and sessions is a list of per-session dicts.
    """
    if not machine:
        machine = get_hostname()

    # Determine if data_dir is a root dir or a project dir
    json_pattern = os.path.join(data_dir, "local_*.json")
    json_files = glob.glob(json_pattern)

    if json_files:
        # Direct project dir
        return _parse_project(data_dir, machine, skip_first_n, project_name)

    # Try auto-discovering org/project subdirs
    all_turns = []
    all_sessions = []
    for org_dir in sorted(glob.glob(os.path.join(data_dir, "*"))):
        if not os.path.isdir(org_dir):
            continue
        for proj_dir in sorted(glob.glob(os.path.join(org_dir, "*"))):
            if not os.path.isdir(proj_dir):
                continue
            pname = project_name or os.path.basename(proj_dir)
            turns, sessions = _parse_project(
                proj_dir, machine, skip_first_n, pname
            )
            all_turns.extend(turns)
            all_sessions.extend(sessions)

    return all_turns, all_sessions


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
        turns_assistant = 0
        input_tokens = 0
        output_tokens = 0
        cache_read_tokens = 0
        cache_create_tokens = 0
        session_turns = []
        assistant_turn_num = 0

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
                    ts_str = (
                        rec.get("_audit_timestamp")
                        or rec.get("message", {}).get("_audit_timestamp")
                    )
                    ts_iso = parse_timestamp(ts_str)

                    if rec_type == "user":
                        content = rec.get("message", {}).get("content", "")
                        if is_genuine_user_turn(content):
                            turns_user += 1

                    elif rec_type == "assistant":
                        turns_assistant += 1
                        assistant_turn_num += 1
                        msg = rec.get("message", {})
                        usage = msg.get("usage", {})
                        mdl = msg.get("model", "")

                        inp = usage.get("input_tokens", 0)
                        out = usage.get("output_tokens", 0)
                        cr = usage.get("cache_read_input_tokens", 0)
                        cc = usage.get("cache_creation_input_tokens", 0)
                        total = inp + out + cr + cc

                        input_tokens += inp
                        output_tokens += out
                        cache_read_tokens += cr
                        cache_create_tokens += cc

                        session_turns.append({
                            "source": source,
                            "machine": machine,
                            "project": project_name,
                            "session_id": session_id,
                            "turn_number": assistant_turn_num,
                            "timestamp": ts_iso,
                            "model": mdl,
                            "model_family": model_family(mdl),
                            "input_tokens": inp,
                            "output_tokens": out,
                            "cache_read_tokens": cr,
                            "cache_create_tokens": cc,
                            "reasoning_output_tokens": 0,
                            "total_tokens": total,
                            "is_subagent": False,
                            "subagent_id": None,
                        })
        except OSError:
            continue

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

        # Determine primary model (most common non-synthetic)
        model_counts = {}
        for t in session_turns:
            m = t["model"]
            if m and m != "<synthetic>":
                model_counts[m] = model_counts.get(m, 0) + 1
        primary_model = ""
        if model_counts:
            primary_model = max(model_counts, key=model_counts.get)

        raw_sessions.append({
            "created_at_ms": created_at_ms,
            "session_id": session_id,
            "turns": session_turns,
            "session_dict": {
                "source": source,
                "machine": machine,
                "project": project_name,
                "session_id": session_id,
                "title": meta.get("title") or "(untitled)",
                "model": primary_model or meta.get("model", ""),
                "created_at": created_at_iso,
                "duration_min": duration_min,
                "turns_user": turns_user,
                "turns_assistant": turns_assistant,
                "total_input_tokens": input_tokens,
                "total_output_tokens": output_tokens,
                "total_cache_read_tokens": cache_read_tokens,
                "total_cache_create_tokens": cache_create_tokens,
                "total_reasoning_output_tokens": 0,
                "total_tokens": input_tokens + output_tokens + cache_read_tokens + cache_create_tokens,
                "subagent_turns": 0,
            },
        })

    # Sort by creation time
    raw_sessions.sort(key=lambda s: s.get("created_at_ms") or 0)

    # Skip first N sessions
    if skip_first_n > 0 and len(raw_sessions) > skip_first_n:
        raw_sessions = raw_sessions[skip_first_n:]

    turns = []
    sessions = []
    for rs in raw_sessions:
        turns.extend(rs["turns"])
        sessions.append(rs["session_dict"])

    print(f"  cowork: {len(sessions)} sessions, {len(turns)} turns", file=sys.stderr)
    return turns, sessions
