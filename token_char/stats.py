"""Stdlib-only statistics for token-char table output."""

import math


def percentile(sorted_vals, pct):
    """Compute percentile from pre-sorted list using linear interpolation.

    Args:
        sorted_vals: Pre-sorted list of numeric values (must be non-empty).
        pct: Percentile in [0, 100].

    Returns:
        Interpolated percentile value.
    """
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    k = (pct / 100) * (n - 1)
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return sorted_vals[lo]
    frac = k - lo
    return sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo])


def percentile_stats(values):
    """Return dict with n, sum, median, mean, p90, p99, max for a list of values.

    Returns dict with all zeros if values is empty.
    """
    if not values:
        return {"n": 0, "sum": 0, "median": 0, "mean": 0, "p90": 0, "p99": 0, "max": 0}
    s = sorted(values)
    total = sum(s)
    n = len(s)
    return {
        "n": n,
        "sum": total,
        "median": percentile(s, 50),
        "mean": total / n,
        "p90": percentile(s, 90),
        "p99": percentile(s, 99),
        "max": s[-1],
    }


def compute_source_stats(turns, sessions):
    """Aggregate stats for one source.

    Args:
        turns: List of turn dicts for a single source.
        sessions: List of session dicts for a single source.

    Returns dict with:
        - source: source name
        - counts: sessions, turns, date range
        - turn_stats: percentile_stats for each token field + total
        - session_stats: turns-per-session and tokens-per-session stats
        - composition: % of total by token type
        - cache_hit_ratio: cache_read / (cache_read + input)
        - turn_profile: % tool-use (output<=10) vs substantive (output>10)
        - substantive_output_stats: percentile_stats for output on substantive turns
        - subagent_turns: count and total
    """
    if not turns and not sessions:
        return None

    source = sessions[0]["source"] if sessions else turns[0]["source"]

    # Date range from sessions
    dates = []
    for s in sessions:
        if s.get("created_at"):
            dates.append(s["created_at"][:10])  # YYYY-MM-DD
    dates.sort()
    date_range = (dates[0], dates[-1]) if dates else (None, None)

    # Turn-level token lists
    input_vals = [t["input_tokens"] for t in turns]
    output_vals = [t["output_tokens"] for t in turns]
    cache_read_vals = [t["cache_read_tokens"] for t in turns]
    cache_create_vals = [t["cache_create_tokens"] for t in turns]
    reasoning_vals = [t["reasoning_output_tokens"] for t in turns]
    total_vals = [t["total_tokens"] for t in turns]

    turn_stats = {
        "cache_read": percentile_stats(cache_read_vals),
        "cache_create": percentile_stats(cache_create_vals),
        "input": percentile_stats(input_vals),
        "output": percentile_stats(output_vals),
        "reasoning_output": percentile_stats(reasoning_vals),
        "total": percentile_stats(total_vals),
    }

    # Session-level stats
    turns_per_session = [s["turns_assistant"] for s in sessions]
    tokens_per_session = [s["total_tokens"] for s in sessions]
    session_stats = {
        "turns_per_session": percentile_stats(turns_per_session),
        "tokens_per_session": percentile_stats(tokens_per_session),
    }

    # Composition
    grand_total = sum(total_vals) if total_vals else 0
    sum_input = sum(input_vals)
    sum_output = sum(output_vals)
    sum_cache_read = sum(cache_read_vals)
    sum_cache_create = sum(cache_create_vals)

    def pct(val):
        return (val / grand_total * 100) if grand_total else 0.0

    composition = {
        "cache_read": pct(sum_cache_read),
        "cache_create": pct(sum_cache_create),
        "input": pct(sum_input),
        "output": pct(sum_output),
    }

    # Cache hit ratio
    cache_denom = sum_cache_read + sum_input
    cache_hit_ratio = (sum_cache_read / cache_denom * 100) if cache_denom else 0.0

    # Turn profile: tool-use (output<=10) vs substantive (output>10)
    tool_use_turns = sum(1 for t in turns if t["output_tokens"] <= 10)
    substantive_turns = len(turns) - tool_use_turns
    turn_profile = {
        "tool_use": tool_use_turns,
        "substantive": substantive_turns,
        "tool_use_pct": (tool_use_turns / len(turns) * 100) if turns else 0.0,
        "substantive_pct": (substantive_turns / len(turns) * 100) if turns else 0.0,
    }

    # Substantive output stats
    substantive_output_vals = [t["output_tokens"] for t in turns if t["output_tokens"] > 10]
    substantive_output_stats = percentile_stats(substantive_output_vals)

    # Subagent info
    subagent_count = sum(1 for t in turns if t.get("is_subagent"))
    total_turns = len(turns)

    # Per-project aggregation for session detail table
    projects = {}
    for s in sessions:
        proj = s.get("project", "(unknown)")
        if proj not in projects:
            projects[proj] = {
                "project": proj,
                "sessions": 0,
                "turns": 0,
                "model": s.get("model", ""),
                "total_tokens": 0,
            }
        projects[proj]["sessions"] += 1
        projects[proj]["turns"] += s["turns_assistant"]
        projects[proj]["total_tokens"] += s["total_tokens"]
        # Use model from latest session
        if s.get("model"):
            projects[proj]["model"] = s["model"]

    return {
        "source": source,
        "counts": {
            "sessions": len(sessions),
            "turns": total_turns,
            "date_start": date_range[0],
            "date_end": date_range[1],
        },
        "turn_stats": turn_stats,
        "session_stats": session_stats,
        "composition": composition,
        "cache_hit_ratio": cache_hit_ratio,
        "turn_profile": turn_profile,
        "substantive_output_stats": substantive_output_stats,
        "subagent_turns": subagent_count,
        "total_turns": total_turns,
        "projects": projects,
    }


def fmt_k(val):
    """Format a number with K/M suffix.

    1234567 -> '1.2M', 45000 -> '45.0K', 800 -> '800'.
    """
    if val is None:
        return "-"
    val = float(val)
    if val >= 1_000_000:
        return f"{val / 1_000_000:.1f}M"
    if val >= 1_000:
        return f"{val / 1_000:.1f}K"
    if val == int(val):
        return f"{int(val)}"
    return f"{val:.1f}"
