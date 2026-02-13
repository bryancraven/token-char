#!/usr/bin/env python3
"""
Self-contained token usage extractor — stdlib only, no install needed.

Designed to be piped via SSH:
    ssh user@host python3 < scripts/remote_extract.py > host.json

Environment variables:
    MACHINE_NAME        Override hostname (default: platform.node())
    TC_SOURCE           cowork, claude_code, or all (default: all)
    TC_COWORK_DIR       Override Cowork data directory
    TC_CLAUDE_CODE_DIR  Override Claude Code projects directory
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
    elif source == "claude_code":
        if system in ("Darwin", "Linux"):
            return os.path.expanduser("~/.claude/projects")
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
            with open(jf, "r") as fh:
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
        turn_num = 0

        try:
            with open(audit_path, "r") as fh:
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
                        turns_assistant += 1
                        turn_num += 1
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
                            "source": source, "machine": machine,
                            "project": project_name, "session_id": session_id,
                            "turn_number": turn_num, "timestamp": ts_iso,
                            "model": mdl, "model_family": model_family(mdl),
                            "input_tokens": inp, "output_tokens": out,
                            "cache_read_tokens": cr, "cache_create_tokens": cc,
                            "total_tokens": total,
                            "is_subagent": False, "subagent_id": None,
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
                created_at_iso = datetime.fromtimestamp(
                    created_at_ms / 1000, tz=timezone.utc
                ).isoformat()
            except (OSError, ValueError, OverflowError):
                pass

        model_counts = {}
        for t in session_turns:
            m = t["model"]
            if m and m != "<synthetic>":
                model_counts[m] = model_counts.get(m, 0) + 1
        primary_model = max(model_counts, key=model_counts.get) if model_counts else meta.get("model", "")

        turns.extend(session_turns)
        sessions.append({
            "source": source, "machine": machine,
            "project": project_name, "session_id": session_id,
            "title": meta.get("title") or "(untitled)",
            "model": primary_model,
            "created_at": created_at_iso, "duration_min": duration_min,
            "turns_user": turns_user, "turns_assistant": turns_assistant,
            "total_input_tokens": input_tokens,
            "total_output_tokens": output_tokens,
            "total_cache_read_tokens": cache_read_tokens,
            "total_cache_create_tokens": cache_create_tokens,
            "total_tokens": input_tokens + output_tokens + cache_read_tokens + cache_create_tokens,
            "subagent_turns": 0,
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
        if dirname.startswith("-"):
            project_name = "/" + dirname[1:].replace("-", "/")
        else:
            project_name = dirname

        for jf in sorted(glob.glob(os.path.join(proj_dir, "*.jsonl"))):
            session_id = os.path.splitext(os.path.basename(jf))[0]

            turns_user = 0
            turns_assistant = 0
            input_tokens = 0
            output_tokens = 0
            cache_read_tokens = 0
            cache_create_tokens = 0
            session_turns = []
            turn_num = 0
            first_user_text = None
            session_model = ""
            first_ts = None
            last_ts = None

            try:
                with open(jf, "r") as fh:
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

                            turns_assistant += 1
                            turn_num += 1
                            mdl = msg.get("model", "")
                            if mdl and mdl != "<synthetic>" and not session_model:
                                session_model = mdl

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
                                "source": source, "machine": machine,
                                "project": project_name, "session_id": session_id,
                                "turn_number": turn_num, "timestamp": ts_iso,
                                "model": mdl, "model_family": model_family(mdl),
                                "input_tokens": inp, "output_tokens": out,
                                "cache_read_tokens": cr, "cache_create_tokens": cc,
                                "total_tokens": total,
                                "is_subagent": False, "subagent_id": None,
                            })
            except OSError:
                continue

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
                    with open(sa_file, "r") as fh:
                        for line in fh:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                rec = json.loads(line)
                            except json.JSONDecodeError:
                                continue

                            rec_type = rec.get("type", "")
                            if rec_type != "assistant":
                                continue

                            msg = rec.get("message", {})
                            usage = msg.get("usage", {})
                            if not usage:
                                continue

                            ts_str = rec.get("timestamp", "")
                            ts_iso = parse_timestamp(ts_str)
                            if ts_iso:
                                if first_ts is None:
                                    first_ts = ts_iso
                                last_ts = ts_iso

                            turns_assistant += 1
                            turn_num += 1
                            subagent_turns_count += 1
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
                                "source": source, "machine": machine,
                                "project": project_name, "session_id": session_id,
                                "turn_number": turn_num, "timestamp": ts_iso,
                                "model": mdl, "model_family": model_family(mdl),
                                "input_tokens": inp, "output_tokens": out,
                                "cache_read_tokens": cr, "cache_create_tokens": cc,
                                "total_tokens": total,
                                "is_subagent": True, "subagent_id": agent_id,
                            })
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
                    delta = (dt_last - dt_first).total_seconds() / 60
                    if delta > 0:
                        duration_min = round(delta, 1)
                except (ValueError, TypeError):
                    pass

            turns.extend(session_turns)
            sessions.append({
                "source": source, "machine": machine,
                "project": project_name, "session_id": session_id,
                "title": title, "model": session_model,
                "created_at": first_ts, "duration_min": duration_min,
                "turns_user": turns_user, "turns_assistant": turns_assistant,
                "total_input_tokens": input_tokens,
                "total_output_tokens": output_tokens,
                "total_cache_read_tokens": cache_read_tokens,
                "total_cache_create_tokens": cache_create_tokens,
                "total_tokens": input_tokens + output_tokens + cache_read_tokens + cache_create_tokens,
                "subagent_turns": subagent_turns_count,
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
