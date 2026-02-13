"""Shared helpers for token-char source parsers."""

import os
import platform
from datetime import datetime, timezone


def parse_timestamp(ts_str):
    """Parse an ISO 8601 timestamp string, handling Z suffix.
    Returns ISO string in UTC or None."""
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, AttributeError):
        return None


def model_family(model_name):
    """Classify a model string into opus/sonnet/haiku/unknown."""
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
    """Returns True if the user message content represents a genuine user turn
    (not a tool_result callback list)."""
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        has_tool_result = any(
            isinstance(c, dict) and c.get("type") == "tool_result"
            for c in content
        )
        return not has_tool_result
    return False


def default_data_dir(source):
    """Return the platform-appropriate default data directory for a source.
    Returns None if not determinable."""
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
        # TODO: Windows support
        return None
    elif source == "claude_code":
        if system in ("Darwin", "Linux"):
            return os.path.expanduser("~/.claude/projects")
        # TODO: Windows support
        return None
    return None


def get_hostname():
    """Return the machine hostname."""
    return platform.node()
