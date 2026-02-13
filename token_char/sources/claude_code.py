"""Claude Code (CLI) session parser."""

import json
import os
import glob
import sys

from ._common import parse_timestamp, model_family, is_genuine_user_turn, get_hostname


def _decode_project_name(dirname):
    """Decode a Claude Code project directory name to a path.
    e.g. '-home-ig88-rpi-deploy' -> '/home/ig88/rpi-deploy'
    On macOS: '-Users-bryanc-dev-foo' -> '/Users/bryanc/dev/foo'
    On Windows: 'C--Users-tmd2p-code' -> 'C:\\Users\\tmd2p\\code'
    """
    # Windows-encoded path: drive letter followed by -- (e.g. C--Users-foo)
    if len(dirname) >= 3 and dirname[0].isalpha() and dirname[1:3] == "--":
        return dirname[0] + ":\\" + dirname[3:].replace("-", "\\")
    # Unix-encoded path: leading dash (e.g. -home-user-project)
    if dirname.startswith("-"):
        return "/" + dirname[1:].replace("-", "/")
    return dirname


def extract_claude_code(projects_dir, project_map=None, machine=""):
    """Extract turns and sessions from Claude Code JSONL session files.

    Args:
        projects_dir: Path to ~/.claude/projects (or equivalent)
        project_map: Optional dict mapping dir names to friendly project names
        machine: Machine name override (default: hostname)

    Returns:
        (turns, sessions) where turns is a list of per-turn dicts
        and sessions is a list of per-session dicts.
    """
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

        # Only glob at project level — do NOT recurse into session/subagents dirs
        for jf in sorted(glob.glob(os.path.join(proj_dir, "*.jsonl"))):
            session_id = os.path.splitext(os.path.basename(jf))[0]

            turns_user = 0
            turns_assistant = 0
            input_tokens = 0
            output_tokens = 0
            cache_read_tokens = 0
            cache_create_tokens = 0
            session_turns = []
            assistant_turn_num = 0
            first_user_text = None
            session_model = ""
            first_ts = None
            last_ts = None
            session_cwd = ""

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

                        # Skip non-message records
                        if rec_type in ("file-history-snapshot", "system"):
                            continue

                        ts_str = rec.get("timestamp", "")
                        ts_iso = parse_timestamp(ts_str)

                        if ts_iso:
                            if first_ts is None:
                                first_ts = ts_iso
                            last_ts = ts_iso

                        # Capture session metadata from any record
                        if not session_cwd and rec.get("cwd"):
                            session_cwd = rec["cwd"]

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
                            assistant_turn_num += 1
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
                                "total_tokens": total,
                                "is_subagent": False,
                                "subagent_id": None,
                            })
            except OSError:
                continue

            # Parse subagent files for this session
            subagent_turns_count = 0
            subagent_pattern = os.path.join(proj_dir, session_id, "subagents", "*.jsonl")
            for sa_file in sorted(glob.glob(subagent_pattern)):
                sa_basename = os.path.basename(sa_file)
                # Extract agent ID: "agent-ab884ec.jsonl" -> "ab884ec"
                sa_name = os.path.splitext(sa_basename)[0]
                if sa_name.startswith("agent-"):
                    agent_id = sa_name[len("agent-"):]
                else:
                    agent_id = sa_name

                try:
                    with open(sa_file, "r", encoding="utf-8") as fh:
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
                            assistant_turn_num += 1
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
                                "total_tokens": total,
                                "is_subagent": True,
                                "subagent_id": agent_id,
                            })
                except OSError:
                    continue

            if not session_turns:
                continue

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
                    delta = (dt_last - dt_first).total_seconds() / 60
                    if delta > 0:
                        duration_min = round(delta, 1)
                except (ValueError, TypeError):
                    pass

            all_turns.extend(session_turns)
            all_sessions.append({
                "source": source,
                "machine": machine,
                "project": project_name,
                "session_id": session_id,
                "title": title,
                "model": session_model,
                "created_at": first_ts,
                "duration_min": duration_min,
                "turns_user": turns_user,
                "turns_assistant": turns_assistant,
                "total_input_tokens": input_tokens,
                "total_output_tokens": output_tokens,
                "total_cache_read_tokens": cache_read_tokens,
                "total_cache_create_tokens": cache_create_tokens,
                "total_tokens": input_tokens + output_tokens + cache_read_tokens + cache_create_tokens,
                "subagent_turns": subagent_turns_count,
            })

    print(
        f"  claude_code: {len(all_sessions)} sessions, {len(all_turns)} turns",
        file=sys.stderr,
    )
    return all_turns, all_sessions
