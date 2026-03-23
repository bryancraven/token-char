"""Tests for Claude Code session parser."""

import os
import shutil

import pytest

from token_char.schema import validate_session, validate_turn
from token_char.sources.claude_code import _decode_project_name, extract_claude_code


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture
def cc_dir(tmp_path):
    proj_dir = tmp_path / "-home-user-project"
    proj_dir.mkdir()
    shutil.copy(
        os.path.join(FIXTURES, "claude_code_session.jsonl"),
        proj_dir / "sess-001.jsonl",
    )
    return str(tmp_path)


@pytest.fixture
def cc_modern_dir(tmp_path):
    proj_dir = tmp_path / "-home-user-project"
    proj_dir.mkdir()
    shutil.copy(
        os.path.join(FIXTURES, "claude_code_modern_session.jsonl"),
        proj_dir / "sess-modern.jsonl",
    )
    return str(tmp_path)


@pytest.fixture
def cc_modern_dir_with_subagent(tmp_path):
    proj_dir = tmp_path / "-home-user-project"
    proj_dir.mkdir()
    shutil.copy(
        os.path.join(FIXTURES, "claude_code_modern_session.jsonl"),
        proj_dir / "sess-modern.jsonl",
    )

    subagent_dir = proj_dir / "sess-modern" / "subagents"
    subagent_dir.mkdir(parents=True)
    shutil.copy(
        os.path.join(FIXTURES, "claude_code_modern_subagent.jsonl"),
        subagent_dir / "agent-ab884ec.jsonl",
    )
    return str(tmp_path)


@pytest.fixture
def cc_stream_dir(tmp_path):
    proj_dir = tmp_path / "-home-user-project"
    proj_dir.mkdir()
    shutil.copy(
        os.path.join(FIXTURES, "claude_code_stream_session.jsonl"),
        proj_dir / "sess-stream.jsonl",
    )
    return str(tmp_path)


def test_basic_extraction_legacy(cc_dir):
    turns, sessions = extract_claude_code(cc_dir, machine="test-host")
    assert len(sessions) == 1
    assert len(turns) == 3


def test_turn_fields_valid(cc_dir):
    turns, _ = extract_claude_code(cc_dir, machine="test-host")
    for turn in turns:
        assert validate_turn(turn) == []


def test_session_fields_valid(cc_dir):
    _, sessions = extract_claude_code(cc_dir, machine="test-host")
    for session in sessions:
        assert validate_session(session) == []


def test_user_turn_counts_legacy(cc_dir):
    _, sessions = extract_claude_code(cc_dir, machine="test")
    assert sessions[0]["turns_user"] == 2


def test_token_sums_legacy(cc_dir):
    _, sessions = extract_claude_code(cc_dir, machine="test")
    session = sessions[0]
    assert session["total_input_tokens"] == 3500
    assert session["total_output_tokens"] == 1900
    assert session["total_cache_read_tokens"] == 900
    assert session["total_cache_create_tokens"] == 250


def test_legacy_output_fields_are_marked_unreliable(cc_dir):
    turns, sessions = extract_claude_code(cc_dir, machine="test")
    assert all(t["output_tokens_reliable"] is False for t in turns)
    assert all(t["output_tokens_source"] == "assistant_snapshot" for t in turns)
    assert sessions[0]["total_output_tokens_reliable"] is False
    assert sessions[0]["total_output_tokens_source"] == "assistant_snapshot"


def test_session_title(cc_dir):
    _, sessions = extract_claude_code(cc_dir, machine="test")
    assert sessions[0]["title"] == "Build the deploy script"


def test_source_machine_and_family(cc_dir):
    turns, sessions = extract_claude_code(cc_dir, machine="pi-host")
    assert all(t["source"] == "claude_code" for t in turns)
    assert all(t["machine"] == "pi-host" for t in turns)
    assert all(t["model_family"] == "sonnet" for t in turns)
    assert sessions[0]["source"] == "claude_code"
    assert sessions[0]["machine"] == "pi-host"


def test_duration(cc_dir):
    _, sessions = extract_claude_code(cc_dir, machine="test")
    assert sessions[0]["duration_min"] == 0.3


def test_main_turns_not_subagent(cc_dir):
    turns, sessions = extract_claude_code(cc_dir, machine="test")
    assert sessions[0]["subagent_turns"] == 0
    for turn in turns:
        assert turn["is_subagent"] is False
        assert turn["subagent_id"] is None


def test_duplicate_assistant_snapshots_collapsed(cc_modern_dir):
    turns, sessions = extract_claude_code(cc_modern_dir, machine="test")
    session = sessions[0]
    assert len(turns) == 3
    assert session["turns_assistant"] == 3
    assert [t["turn_number"] for t in turns] == [1, 2, 3]
    assert session["total_input_tokens"] == 3500
    assert session["total_output_tokens"] == 2000
    assert session["total_cache_read_tokens"] == 1100
    assert session["total_cache_create_tokens"] == 250


def test_modern_turns_are_unreliable_without_message_delta(cc_modern_dir):
    turns, sessions = extract_claude_code(cc_modern_dir, machine="test")
    assert [t["output_tokens"] for t in turns] == [400, 700, 900]
    assert all(t["output_tokens_reliable"] is False for t in turns)
    assert sessions[0]["total_output_tokens_reliable"] is False
    assert sessions[0]["total_output_tokens_source"] == "assistant_snapshot"


def test_subagent_duplicate_snapshots_collapsed(cc_modern_dir_with_subagent):
    turns, sessions = extract_claude_code(cc_modern_dir_with_subagent, machine="test")
    session = sessions[0]
    assert len(turns) == 5
    assert session["turns_assistant"] == 5
    assert session["subagent_turns"] == 2
    assert [t["turn_number"] for t in turns] == [1, 2, 3, 4, 5]

    subagent_turns = [t for t in turns if t["is_subagent"]]
    assert len(subagent_turns) == 2
    assert all(t["subagent_id"] == "ab884ec" for t in subagent_turns)
    assert all(t["model_family"] == "haiku" for t in subagent_turns)

    assert session["total_input_tokens"] == 3500 + 500
    assert session["total_output_tokens"] == 2000 + 250
    assert session["total_cache_read_tokens"] == 1100 + 125
    assert session["total_cache_create_tokens"] == 250 + 30


def test_stream_message_delta_recovers_per_turn_output(cc_stream_dir):
    turns, sessions = extract_claude_code(cc_stream_dir, machine="test")
    session = sessions[0]

    assert len(turns) == 2
    assert [t["output_tokens"] for t in turns] == [150, 275]
    assert all(t["output_tokens_reliable"] is True for t in turns)
    assert all(t["output_tokens_source"] == "message_delta" for t in turns)
    assert session["total_output_tokens"] == 425
    assert session["total_output_tokens_reliable"] is True
    assert session["total_output_tokens_source"] == "result"


def test_reasoning_output_tokens_zero(cc_dir):
    turns, sessions = extract_claude_code(cc_dir, machine="test")
    assert all(t["reasoning_output_tokens"] == 0 for t in turns)
    assert sessions[0]["total_reasoning_output_tokens"] == 0


def test_empty_projects_dir(tmp_path):
    turns, sessions = extract_claude_code(str(tmp_path), machine="test")
    assert turns == []
    assert sessions == []


def test_nonexistent_dir():
    turns, sessions = extract_claude_code("/nonexistent/path", machine="test")
    assert turns == []
    assert sessions == []


def test_project_name_decoding():
    assert _decode_project_name("-home-ig88-project") == "/home/ig88/project"
    assert _decode_project_name("-Users-alice-dev-foo") == "/Users/alice/dev/foo"
    assert _decode_project_name("some-dir") == "some-dir"


def test_project_name_decoding_windows():
    assert _decode_project_name("C--Users-bob-code") == "C:\\Users\\bob\\code"
    assert _decode_project_name("D--projects-my-app") == "D:\\projects\\my\\app"
    assert _decode_project_name("C--Users-bob") == "C:\\Users\\bob"


def test_project_map(cc_dir):
    turns, sessions = extract_claude_code(
        cc_dir,
        project_map={"-home-user-project": "my_project"},
        machine="test",
    )
    assert sessions[0]["project"] == "my_project"
    assert all(t["project"] == "my_project" for t in turns)
