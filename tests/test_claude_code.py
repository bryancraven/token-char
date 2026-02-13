"""Tests for Claude Code session parser."""

import json
import os
import shutil

import pytest

from token_char.sources.claude_code import extract_claude_code, _decode_project_name
from token_char.schema import validate_turn, validate_session


@pytest.fixture
def cc_dir(tmp_path):
    """Set up a fake Claude Code projects directory with fixture data."""
    fixtures = os.path.join(os.path.dirname(__file__), "fixtures")

    proj_dir = tmp_path / "-home-user-project"
    proj_dir.mkdir()

    shutil.copy(
        os.path.join(fixtures, "claude_code_session.jsonl"),
        proj_dir / "sess-001.jsonl",
    )

    return str(tmp_path)


def test_basic_extraction(cc_dir):
    """Test that we get the right number of turns and sessions."""
    turns, sessions = extract_claude_code(cc_dir, machine="test-host")

    assert len(sessions) == 1
    assert len(turns) == 3  # 3 assistant turns


def test_turn_fields_valid(cc_dir):
    """All turns pass schema validation."""
    turns, _ = extract_claude_code(cc_dir, machine="test-host")
    for t in turns:
        errors = validate_turn(t)
        assert errors == [], f"Turn {t['turn_number']} invalid: {errors}"


def test_session_fields_valid(cc_dir):
    """Session passes schema validation."""
    _, sessions = extract_claude_code(cc_dir, machine="test-host")
    for s in sessions:
        errors = validate_session(s)
        assert errors == [], f"Session {s['session_id']} invalid: {errors}"


def test_skips_system_and_file_history(cc_dir):
    """system and file-history-snapshot records should not produce turns."""
    turns, _ = extract_claude_code(cc_dir, machine="test")
    # Only 3 assistant turns, not 5 records
    assert len(turns) == 3


def test_user_turn_counts(cc_dir):
    """Tool_result callbacks should not count as user turns."""
    _, sessions = extract_claude_code(cc_dir, machine="test")
    s = sessions[0]
    # 2 genuine user messages, 1 tool_result skipped
    assert s["turns_user"] == 2


def test_token_sums(cc_dir):
    """Verify token totals are summed correctly."""
    _, sessions = extract_claude_code(cc_dir, machine="test")
    s = sessions[0]

    assert s["total_input_tokens"] == 800 + 1200 + 1500    # = 3500
    assert s["total_output_tokens"] == 400 + 600 + 900     # = 1900
    assert s["total_cache_read_tokens"] == 100 + 300 + 500  # = 900
    assert s["total_cache_create_tokens"] == 50 + 80 + 120  # = 250


def test_session_title(cc_dir):
    """Session title should be derived from first user message."""
    _, sessions = extract_claude_code(cc_dir, machine="test")
    assert sessions[0]["title"] == "Build the deploy script"


def test_source_and_machine(cc_dir):
    """Verify source and machine fields."""
    turns, sessions = extract_claude_code(cc_dir, machine="pi-host")
    assert all(t["source"] == "claude_code" for t in turns)
    assert all(t["machine"] == "pi-host" for t in turns)
    assert sessions[0]["source"] == "claude_code"
    assert sessions[0]["machine"] == "pi-host"


def test_project_name_decoding():
    """Test directory name to path decoding.
    Note: decoding is lossy — hyphens in path segments become slashes.
    Use --project-map for exact names."""
    assert _decode_project_name("-home-ig88-project") == "/home/ig88/project"
    assert _decode_project_name("-Users-bryanc-dev-foo") == "/Users/bryanc/dev/foo"
    assert _decode_project_name("some-dir") == "some-dir"


def test_project_map(cc_dir):
    """project_map should override decoded project names."""
    turns, sessions = extract_claude_code(
        cc_dir,
        project_map={"-home-user-project": "my_project"},
        machine="test",
    )
    assert sessions[0]["project"] == "my_project"
    assert all(t["project"] == "my_project" for t in turns)


def test_model_family(cc_dir):
    """All turns should be classified as sonnet."""
    turns, _ = extract_claude_code(cc_dir, machine="test")
    assert all(t["model_family"] == "sonnet" for t in turns)


def test_duration(cc_dir):
    """Duration should be computed from first to last timestamp."""
    _, sessions = extract_claude_code(cc_dir, machine="test")
    # 10:00:01 to 10:00:20 = 19 seconds = 0.3 minutes
    assert sessions[0]["duration_min"] == 0.3


def test_subagent_files_not_globbed(cc_dir):
    """JSONL files in session subdirs (subagents) should not be parsed."""
    # Create a subagent dir with a JSONL file
    subagent_dir = os.path.join(cc_dir, "-home-user-project", "sess-001", "subagents")
    os.makedirs(subagent_dir)
    with open(os.path.join(subagent_dir, "sub.jsonl"), "w") as f:
        f.write(json.dumps({
            "type": "assistant",
            "message": {
                "model": "claude-haiku-3-5-20241022",
                "usage": {"input_tokens": 99, "output_tokens": 99,
                           "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                "content": [],
            },
            "timestamp": "2025-02-13T10:05:00Z",
        }) + "\n")

    turns, sessions = extract_claude_code(cc_dir, machine="test")
    # Should still be only 3 turns, 1 session — subagent file ignored
    assert len(turns) == 3
    assert len(sessions) == 1


def test_empty_projects_dir(tmp_path):
    """Empty projects dir returns empty results."""
    turns, sessions = extract_claude_code(str(tmp_path), machine="test")
    assert len(turns) == 0
    assert len(sessions) == 0


def test_nonexistent_dir():
    """Nonexistent dir returns empty results."""
    turns, sessions = extract_claude_code("/nonexistent/path", machine="test")
    assert len(turns) == 0
    assert len(sessions) == 0
