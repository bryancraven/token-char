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


@pytest.fixture
def cc_dir_with_subagent(tmp_path):
    """Set up a fake Claude Code projects directory with main + subagent data."""
    fixtures = os.path.join(os.path.dirname(__file__), "fixtures")

    proj_dir = tmp_path / "-home-user-project"
    proj_dir.mkdir()

    shutil.copy(
        os.path.join(fixtures, "claude_code_session.jsonl"),
        proj_dir / "sess-001.jsonl",
    )

    subagent_dir = proj_dir / "sess-001" / "subagents"
    subagent_dir.mkdir(parents=True)
    shutil.copy(
        os.path.join(fixtures, "claude_code_subagent.jsonl"),
        subagent_dir / "agent-ab884ec.jsonl",
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


def test_project_name_decoding_windows():
    """Test Windows-style directory name decoding.
    Windows uses drive letter + double dash (e.g. C--Users-foo)."""
    assert _decode_project_name("C--Users-tmd2p-code") == "C:\\Users\\tmd2p\\code"
    assert _decode_project_name("D--projects-my-app") == "D:\\projects\\my\\app"
    assert _decode_project_name("C--Users-tmd2p") == "C:\\Users\\tmd2p"


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


def test_main_turns_not_subagent(cc_dir):
    """Main session turns should have is_subagent=False and subagent_id=None."""
    turns, _ = extract_claude_code(cc_dir, machine="test")
    for t in turns:
        assert t["is_subagent"] is False
        assert t["subagent_id"] is None


def test_session_subagent_turns_zero(cc_dir):
    """Sessions without subagents should have subagent_turns=0."""
    _, sessions = extract_claude_code(cc_dir, machine="test")
    assert sessions[0]["subagent_turns"] == 0


def test_subagent_files_included(cc_dir_with_subagent):
    """JSONL files in session subagents dirs should now be parsed."""
    turns, sessions = extract_claude_code(cc_dir_with_subagent, machine="test")
    # 3 main turns + 2 subagent turns = 5 total
    assert len(turns) == 5
    assert len(sessions) == 1


def test_subagent_turn_fields(cc_dir_with_subagent):
    """Subagent turns should have is_subagent=True and correct subagent_id."""
    turns, _ = extract_claude_code(cc_dir_with_subagent, machine="test")
    subagent_turns = [t for t in turns if t["is_subagent"]]
    assert len(subagent_turns) == 2

    for t in subagent_turns:
        assert t["is_subagent"] is True
        assert t["subagent_id"] == "ab884ec"
        assert t["model_family"] == "haiku"
        assert t["model"] == "claude-haiku-4-5-20251001"
        errors = validate_turn(t)
        assert errors == [], f"Subagent turn invalid: {errors}"


def test_subagent_tokens_in_session(cc_dir_with_subagent):
    """Session aggregates should include subagent tokens."""
    _, sessions = extract_claude_code(cc_dir_with_subagent, machine="test")
    s = sessions[0]

    # Main: input=3500, output=1900, cache_read=900, cache_create=250
    # Subagent: input=200+300=500, output=100+150=250, cache_read=50+75=125, cache_create=10+20=30
    assert s["total_input_tokens"] == 3500 + 500
    assert s["total_output_tokens"] == 1900 + 250
    assert s["total_cache_read_tokens"] == 900 + 125
    assert s["total_cache_create_tokens"] == 250 + 30
    assert s["subagent_turns"] == 2
    assert s["turns_assistant"] == 5  # 3 main + 2 subagent


def test_subagent_turn_numbering(cc_dir_with_subagent):
    """Subagent turns should continue numbering after main turns."""
    turns, _ = extract_claude_code(cc_dir_with_subagent, machine="test")
    turn_numbers = [t["turn_number"] for t in turns]
    # Main turns: 1, 2, 3; subagent turns: 4, 5
    assert turn_numbers == [1, 2, 3, 4, 5]


def test_reasoning_output_tokens_zero(cc_dir):
    """Claude Code turns should have reasoning_output_tokens=0."""
    turns, sessions = extract_claude_code(cc_dir, machine="test")
    for t in turns:
        assert t["reasoning_output_tokens"] == 0
    assert sessions[0]["total_reasoning_output_tokens"] == 0


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
