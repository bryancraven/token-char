"""CLI entry point for token-char extraction."""

import argparse
import os
import sys

from . import __version__
from .sources._common import default_data_dir, get_hostname
from .sources.cowork import extract_cowork
from .sources.claude_code import extract_claude_code
from .sources.codex import extract_codex
from .output import write_json, write_csv, write_jsonl


def build_parser():
    p = argparse.ArgumentParser(
        prog="token_char.extract",
        description="Extract token usage data from Claude Desktop (Cowork) and Claude Code sessions.",
    )
    p.add_argument(
        "--source",
        choices=["cowork", "claude_code", "codex", "all"],
        default="all",
        help="Which source to extract (default: all)",
    )
    p.add_argument(
        "--cowork-dir",
        metavar="PATH",
        help="Override Cowork data directory",
    )
    p.add_argument(
        "--claude-code-dir",
        metavar="PATH",
        help="Override Claude Code projects directory",
    )
    p.add_argument(
        "--codex-dir",
        metavar="PATH",
        help="Override Codex sessions directory",
    )
    p.add_argument(
        "--output",
        metavar="PATH",
        help="Output file or directory (default: stdout)",
    )
    p.add_argument(
        "--format",
        choices=["json", "csv", "jsonl", "table"],
        default="json",
        help="Output format (default: json)",
    )
    p.add_argument(
        "--detail",
        choices=["sessions", "all"],
        default="sessions",
        help="Detail level for table format (default: sessions)",
    )
    p.add_argument(
        "--machine",
        metavar="NAME",
        help="Machine name override (default: hostname)",
    )
    p.add_argument(
        "--project-map",
        metavar="KEY=VAL",
        action="append",
        default=[],
        help="Map directory names to friendly project names (repeatable)",
    )
    p.add_argument(
        "--skip-first-n",
        type=int,
        default=0,
        metavar="N",
        help="Skip N oldest Cowork sessions",
    )
    p.add_argument(
        "--ascii",
        action="store_true",
        help="Force ASCII output (no Unicode box-drawing characters)",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress stderr progress messages",
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"token-char {__version__}",
    )
    return p


def _ensure_utf8_stdout():
    """On Windows, reconfigure stdout to UTF-8 if possible."""
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass  # Fall through — _detect_charset will pick ASCII


def main(argv=None):
    _ensure_utf8_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)

    machine = args.machine or get_hostname()

    # Suppress stderr if quiet
    if args.quiet:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    # Parse project map
    project_map = {}
    for kv in args.project_map:
        if "=" in kv:
            k, v = kv.split("=", 1)
            project_map[k] = v

    all_turns = []
    all_sessions = []
    sources_used = []

    # Extract Cowork
    if args.source in ("cowork", "all"):
        cowork_dir = args.cowork_dir or default_data_dir("cowork")
        if cowork_dir and os.path.isdir(cowork_dir):
            turns, sessions = extract_cowork(
                cowork_dir,
                skip_first_n=args.skip_first_n,
                machine=machine,
            )
            all_turns.extend(turns)
            all_sessions.extend(sessions)
            if turns or sessions:
                sources_used.append("cowork")
        else:
            print("  cowork: data directory not found, skipping", file=sys.stderr)

    # Extract Claude Code
    if args.source in ("claude_code", "all"):
        cc_dir = args.claude_code_dir or default_data_dir("claude_code")
        if cc_dir and os.path.isdir(cc_dir):
            turns, sessions = extract_claude_code(
                cc_dir,
                project_map=project_map,
                machine=machine,
            )
            all_turns.extend(turns)
            all_sessions.extend(sessions)
            if turns or sessions:
                sources_used.append("claude_code")
        else:
            print("  claude_code: data directory not found, skipping", file=sys.stderr)

    # Extract Codex
    if args.source in ("codex", "all"):
        codex_dir = args.codex_dir or default_data_dir("codex")
        if codex_dir and os.path.isdir(codex_dir):
            turns, sessions = extract_codex(
                codex_dir,
                machine=machine,
            )
            all_turns.extend(turns)
            all_sessions.extend(sessions)
            if turns or sessions:
                sources_used.append("codex")
        else:
            print("  codex: data directory not found, skipping", file=sys.stderr)

    print(
        f"Total: {len(all_turns)} turns, {len(all_sessions)} sessions",
        file=sys.stderr,
    )

    # Output
    fmt = args.format
    dest = args.output

    if fmt == "json":
        out_path = None
        if dest:
            if os.path.isdir(dest):
                out_path = os.path.join(dest, "token_char.json")
            else:
                out_path = dest
        write_json(all_turns, all_sessions, out_path, machine, sources_used, __version__)

    elif fmt == "csv":
        if not dest:
            print("error: --output required for CSV format", file=sys.stderr)
            sys.exit(1)
        if os.path.isdir(dest):
            write_csv(all_turns, all_sessions, dest)
        else:
            write_csv(all_turns, all_sessions, dest)

    elif fmt == "jsonl":
        out_path = None
        if dest:
            if os.path.isdir(dest):
                out_path = os.path.join(dest, "token_char.jsonl")
            else:
                out_path = dest
        write_jsonl(all_turns, all_sessions, out_path)

    elif fmt == "table":
        if dest:
            print("warning: --output ignored for table format (writes to stdout)", file=sys.stderr)
        from .table import write_table
        write_table(all_turns, all_sessions, detail=args.detail, ascii=args.ascii)


if __name__ == "__main__":
    main()
