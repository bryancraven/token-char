"""Human-readable table formatter for token-char."""

import os
import sys

from .stats import compute_source_stats, fmt_k


class _CharSet:
    """Character set for table rendering — Unicode or ASCII fallback."""

    def __init__(self, double_line, thin_line, vertical, corner, dagger, lte):
        self.double_line = double_line   # ═ or =
        self.thin_line = thin_line       # ─ or -
        self.vertical = vertical         # │ or |
        self.corner = corner             # └─ or +-
        self.dagger = dagger             # † or *
        self.lte = lte                   # ≤ or <=


UNICODE_CHARS = _CharSet("\u2550", "\u2500", "\u2502", "\u2514\u2500", "\u2020", "\u2264")
ASCII_CHARS = _CharSet("=", "-", "|", "+-", "*", "<=")


def _detect_charset(file):
    """Return UNICODE_CHARS if file's encoding supports box-drawing, else ASCII_CHARS."""
    enc = getattr(file, "encoding", None) or ""
    if enc.lower().replace("-", "") in ("utf8", "utf16", "utf32", "utf8sig"):
        return UNICODE_CHARS
    # Try encoding a box char — if it fails, fall back to ASCII
    try:
        "\u2550\u2502".encode(enc)
        return UNICODE_CHARS
    except (UnicodeEncodeError, LookupError):
        return ASCII_CHARS

SOURCE_LABELS = {
    "cowork": "Claude Desktop (Cowork)",
    "claude_code": "Claude Code (CLI)",
    "codex": "OpenAI Codex",
}
SOURCE_ORDER = ["cowork", "claude_code", "codex"]


def _num(val, width=8):
    """Right-align a formatted number in the given width."""
    return fmt_k(val).rjust(width)


def _commas(n):
    """Format integer with commas: 9705 -> '9,705'."""
    return f"{int(n):,}"


def _pct(val, decimals=1):
    """Format percentage: 91.3 -> '91.3%'."""
    return f"{val:.{decimals}f}%"


def _short_project(name):
    """Shorten project name for display: use basename for paths, passthrough otherwise."""
    if not name or name == "(unknown)":
        return name or "(unknown)"
    # Looks like an absolute path — use basename
    if name.startswith("/") or (len(name) >= 3 and name[1] == ":" and name[2] in ("/", "\\")):
        base = os.path.basename(name)
        return base if base else name
    return name


def _header_line(width, cs):
    return cs.double_line * width


def _thin_line(width, cs):
    return cs.thin_line * width


def _write_source_block(stats, out, cs):
    """Write a per-source summary block."""
    source = stats["source"]
    label = SOURCE_LABELS.get(source, source)
    c = stats["counts"]
    is_codex = source == "codex"
    is_claude_code = source == "claude_code"

    # Date range display
    if c["date_start"] and c["date_end"]:
        if c["date_start"] == c["date_end"]:
            date_str = c["date_start"]
        else:
            date_str = f"{c['date_start']} to {c['date_end']}"
    else:
        date_str = "unknown dates"

    # Header
    w = 74
    out.write(f"\n  {_header_line(w, cs)}\n")
    out.write(
        f"  {label}    {_commas(c['sessions'])} sessions {cs.vertical} "
        f"{_commas(c['turns'])} turns {cs.vertical} {date_str}\n"
    )
    out.write(f"  {_header_line(w, cs)}\n\n")

    # Token/Turn stats table
    ts = stats["turn_stats"]
    col_w = 8
    label_w = 27

    if is_codex:
        turn_label = "Tokens/Turn (API call)"
    else:
        turn_label = "Tokens/Turn (assistant)"

    hdr = f"  {turn_label:<{label_w}}  {'Median':>{col_w}}  {'Mean':>{col_w}}  {'P90':>{col_w}}  {'P99':>{col_w}}  {'Max':>{col_w}}"
    out.write(hdr + "\n")
    sep = cs.thin_line
    out.write(f"  {sep * label_w}  {sep * col_w}  {sep * col_w}  {sep * col_w}  {sep * col_w}  {sep * col_w}\n")

    def _stat_row(label, st):
        out.write(f"  {label:<{label_w}}  {_num(st['median'], col_w)}  {_num(st['mean'], col_w)}  {_num(st['p90'], col_w)}  {_num(st['p99'], col_w)}  {_num(st['max'], col_w)}\n")

    def _dash_row(label):
        dash = "-".rjust(col_w)
        out.write(f"  {label:<{label_w}}  {dash}  {dash}  {dash}  {dash}  {dash}\n")

    # Rows — order by typical magnitude: cache_read, cache_create, input, output
    _stat_row("Cache Read", ts["cache_read"])

    if is_codex:
        # Codex: no cache_create, add reasoning sub-row
        _stat_row("Input (novel)", ts["input"])
        _stat_row("Output (incl. reasoning)", ts["output"])
        if ts["reasoning_output"]["max"] > 0:
            _stat_row(f"  {cs.corner} of which reasoning", ts["reasoning_output"])
        _dash_row("Cache Create")
        out.write(f"  {'  ' + cs.dagger + ' not reported by OpenAI API'}\n")
    else:
        _stat_row("Cache Create", ts["cache_create"])
        _stat_row("Input (novel)", ts["input"])
        _stat_row("Output", ts["output"])

    _stat_row("Total", ts["total"])
    out.write("\n")

    # Session stats (only if >1 session)
    if stats["counts"]["sessions"] > 0:
        ss = stats["session_stats"]
        out.write(f"  {'Sessions':<{label_w}}  {'Median':>{col_w}}  {'Mean':>{col_w}}  {'P90':>{col_w}}  {'P99':>{col_w}}  {'Max':>{col_w}}\n")
        out.write(f"  {sep * label_w}  {sep * col_w}  {sep * col_w}  {sep * col_w}  {sep * col_w}  {sep * col_w}\n")
        _stat_row("Turns per session", ss["turns_per_session"])
        _stat_row("Tokens per session", ss["tokens_per_session"])
        out.write("\n")

    # Composition line
    comp = stats["composition"]
    if is_codex:
        out.write(
            f"  Composition: cache_read {_pct(comp['cache_read'])} {cs.vertical} "
            f"input {_pct(comp['input'])} {cs.vertical} "
            f"output {_pct(comp['output'])}\n"
        )
    else:
        out.write(
            f"  Composition: cache_read {_pct(comp['cache_read'])} {cs.vertical} "
            f"cache_create {_pct(comp['cache_create'])} {cs.vertical} "
            f"input {_pct(comp['input'])} {cs.vertical} "
            f"output {_pct(comp['output'])}\n"
        )

    # Cache hit ratio (skip for codex if meaningless)
    if not is_codex:
        out.write(f"  Cache hit ratio: {_pct(stats['cache_hit_ratio'])}\n")

    # Turn profile (skip for codex — all turns are entire tasks)
    if not is_codex:
        tp = stats["turn_profile"]
        out.write(
            f"  Turn profile: {tp['tool_use_pct']:.0f}% tool-use "
            f"({cs.lte}10 output tokens) {cs.vertical} "
            f"{tp['substantive_pct']:.0f}% substantive\n"
        )
        # Substantive output stats
        sout = stats["substantive_output_stats"]
        if sout["n"] > 0:
            out.write(
                f"  Substantive output (>10 tokens):  "
                f"median={fmt_k(sout['median'])}  mean={fmt_k(sout['mean'])}  "
                f"p90={fmt_k(sout['p90'])}  max={fmt_k(sout['max'])}\n"
            )

    # Subagent line (claude_code only)
    if is_claude_code and stats["total_turns"] > 0:
        sa = stats["subagent_turns"]
        total = stats["total_turns"]
        sa_pct = (sa / total * 100) if total else 0
        out.write(f"  Subagent turns: {_commas(sa)} of {_commas(total)} ({sa_pct:.1f}%)\n")

    # Codex footnotes
    if is_codex:
        out.write(f"\n  * Codex turns = per-API-call when available (Desktop/VSCode sessions),\n")
        out.write(f"    per-task for older CLI sessions. Token totals are exact either way.\n")

    out.write("\n")


def _write_session_detail(stats, sessions, out, cs):
    """Write per-session detail table for one source."""
    source = stats["source"]
    label = SOURCE_LABELS.get(source, source)
    is_claude_code = source == "claude_code"

    # Filter sessions for this source and sort by created_at
    src_sessions = [s for s in sessions if s["source"] == source]
    src_sessions.sort(key=lambda s: s.get("created_at") or "")

    if not src_sessions:
        return

    out.write(f"  Sessions {cs.thin_line}{cs.thin_line} {label}\n")
    out.write(f"  {_thin_line(74, cs)}\n")

    # Column headers
    if is_claude_code:
        hdr = f"  {'#':>3}  {'Project':<14}  {'Title':<22}  {'Model':<8}  {'U/A(+S)':>9}  {'Total':>8}"
    else:
        hdr = f"  {'#':>3}  {'Project':<14}  {'Title':<22}  {'Model':<8}  {'U/A':>5}  {'Total':>8}"
    out.write(hdr + "\n")

    for i, s in enumerate(src_sessions, 1):
        proj = _short_project(s.get("project"))[:14]
        title = (s.get("title") or "")[:22]
        if len(s.get("title") or "") > 22:
            title = title[:19] + "..."

        # Model family short label
        model = s.get("model", "")
        for fam in ("opus", "sonnet", "haiku", "gpt"):
            if fam in model.lower():
                model = fam
                break
        else:
            model = model[:8]

        u = s["turns_user"]
        a = s["turns_assistant"]
        total = fmt_k(s["total_tokens"])

        if is_claude_code and s.get("subagent_turns", 0) > 0:
            turns_str = f"{u}/{a}(+{s['subagent_turns']})"
        else:
            turns_str = f"{u}/{a}"

        if is_claude_code:
            out.write(f"  {i:>3}  {proj:<14}  {title:<22}  {model:<8}  {turns_str:>9}  {total:>8}\n")
        else:
            out.write(f"  {i:>3}  {proj:<14}  {title:<22}  {model:<8}  {turns_str:>5}  {total:>8}\n")

    out.write("\n")


def _write_project_summary(stats, out, cs):
    """Write per-project summary table for one source."""
    projects = stats.get("projects", {})
    if not projects:
        return

    source = stats["source"]
    label = SOURCE_LABELS.get(source, source)

    out.write(f"  Projects {cs.thin_line}{cs.thin_line} {label}\n")
    out.write(f"  {_thin_line(74, cs)}\n")

    hdr = f"  {'Project':<16}  {'Sessions':>8}  {'Turns':>6}  {'Model':<8}  {'Total Tokens':>12}"
    out.write(hdr + "\n")

    for proj_name in sorted(projects.keys()):
        p = projects[proj_name]
        display_name = _short_project(proj_name)
        # Model family short label
        model = p.get("model", "")
        for fam in ("opus", "sonnet", "haiku", "gpt"):
            if fam in model.lower():
                model = fam
                break
        else:
            model = model[:8]

        out.write(
            f"  {display_name[:16]:<16}  {p['sessions']:>8}  {p['turns']:>6}  "
            f"{model:<8}  {fmt_k(p['total_tokens']):>12}\n"
        )

    out.write("\n")


def _write_grand_total(all_stats, out, cs):
    """Write grand total footer."""
    total_sources = len(all_stats)
    total_sessions = sum(s["counts"]["sessions"] for s in all_stats)
    total_turns = sum(s["counts"]["turns"] for s in all_stats)
    total_tokens = sum(s["turn_stats"]["total"]["sum"] for s in all_stats)

    out.write(f"  {_thin_line(74, cs)}\n")
    out.write(
        f"  GRAND TOTAL   {total_sources} sources {cs.vertical} "
        f"{_commas(total_sessions)} sessions {cs.vertical} "
        f"{_commas(total_turns)} turns {cs.vertical} "
        f"Total: {fmt_k(total_tokens)} tokens\n"
    )

    # Per-source breakdown
    parts = []
    for s in all_stats:
        label = SOURCE_LABELS.get(s["source"], s["source"]).split("(")[0].strip()
        tok = s["turn_stats"]["total"]["sum"]
        pct = (tok / total_tokens * 100) if total_tokens else 0
        parts.append(f"{label}: {fmt_k(tok)} ({pct:.0f}%)")
    if parts:
        joiner = f" {cs.vertical}  "
        out.write(f"  {joiner.join(parts)}\n")

    out.write("\n")


def write_table(turns, sessions, detail="sessions", file=None, ascii=False):
    """Main entry point. Render human-readable table to file (default stdout).

    Args:
        turns: List of all turn dicts.
        sessions: List of all session dicts.
        detail: Level of detail — "sessions", "turns", or "all".
        file: File object to write to (default: sys.stdout).
        ascii: Force ASCII output (no Unicode box-drawing characters).
    """
    out = file or sys.stdout
    cs = ASCII_CHARS if ascii else _detect_charset(out)

    # Group by source
    sources_present = []
    for src in SOURCE_ORDER:
        src_turns = [t for t in turns if t["source"] == src]
        src_sessions = [s for s in sessions if s["source"] == src]
        if src_turns or src_sessions:
            sources_present.append(src)

    if not sources_present:
        out.write("  No data found.\n")
        return

    # Compute stats per source and render blocks
    all_stats = []
    for src in sources_present:
        src_turns = [t for t in turns if t["source"] == src]
        src_sessions = [s for s in sessions if s["source"] == src]
        stats = compute_source_stats(src_turns, src_sessions)
        if stats:
            all_stats.append(stats)
            _write_source_block(stats, out, cs)
            _write_project_summary(stats, out, cs)

    # Session detail
    if detail in ("sessions", "all"):
        for stats in all_stats:
            _write_session_detail(stats, sessions, out, cs)

    # Grand total
    if len(all_stats) > 0:
        _write_grand_total(all_stats, out, cs)
