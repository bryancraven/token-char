"""Output writers for token-char: JSON, CSV, JSONL."""

import csv
import json
import os
import sys
from datetime import datetime, timezone

from .schema import TURN_FIELDS, SESSION_FIELDS


def write_json(turns, sessions, dest=None, machine="", sources=None, version="0.1.0"):
    """Write the full JSON envelope to dest (file path or None for stdout).

    Args:
        turns: List of turn dicts
        sessions: List of session dicts
        dest: File path or None for stdout
        machine: Machine hostname
        sources: List of source names included
        version: token_char version string
    """
    envelope = {
        "token_char_version": version,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "machine": machine,
        "sources": sources or [],
        "turns": [{k: t.get(k) for k in TURN_FIELDS} for t in turns],
        "sessions": [{k: s.get(k) for k in SESSION_FIELDS} for s in sessions],
    }

    text = json.dumps(envelope, indent=2, default=str)

    if dest:
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        with open(dest, "w") as f:
            f.write(text)
            f.write("\n")
    else:
        sys.stdout.write(text)
        sys.stdout.write("\n")


def write_csv(turns, sessions, prefix):
    """Write turns and sessions to CSV files.

    Args:
        turns: List of turn dicts
        sessions: List of session dicts
        prefix: File path prefix. Produces {prefix}_turns.csv and {prefix}_sessions.csv.
            If prefix is a directory, writes turns.csv and sessions.csv in that dir.
    """
    if os.path.isdir(prefix):
        turns_path = os.path.join(prefix, "turns.csv")
        sessions_path = os.path.join(prefix, "sessions.csv")
    else:
        turns_path = f"{prefix}_turns.csv"
        sessions_path = f"{prefix}_sessions.csv"

    os.makedirs(os.path.dirname(turns_path) or ".", exist_ok=True)

    with open(turns_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TURN_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for t in turns:
            writer.writerow({k: t.get(k, "") for k in TURN_FIELDS})

    with open(sessions_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SESSION_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for s in sessions:
            writer.writerow({k: s.get(k, "") for k in SESSION_FIELDS})

    print(f"  wrote {turns_path}", file=sys.stderr)
    print(f"  wrote {sessions_path}", file=sys.stderr)


def write_jsonl(turns, sessions, dest=None):
    """Write turns and sessions as JSONL (one record per line).

    Each line includes a '_record_type' field ('turn' or 'session').

    Args:
        turns: List of turn dicts
        sessions: List of session dicts
        dest: File path or None for stdout
    """
    lines = []
    for t in turns:
        rec = {k: t.get(k) for k in TURN_FIELDS}
        rec["_record_type"] = "turn"
        lines.append(json.dumps(rec, default=str))
    for s in sessions:
        rec = {k: s.get(k) for k in SESSION_FIELDS}
        rec["_record_type"] = "session"
        lines.append(json.dumps(rec, default=str))

    text = "\n".join(lines) + "\n"

    if dest:
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        with open(dest, "w") as f:
            f.write(text)
    else:
        sys.stdout.write(text)
