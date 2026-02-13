"""Tests for Codex session parser."""

import json
import os
import shutil

import pytest

from token_char.sources.codex import extract_codex, _project_from_cwd
from token_char.sources._common import model_family
from token_char.schema import validate_turn, validate_session


@pytest.fixture
def codex_dir(tmp_path):
    """Set up a fake Codex sessions directory with fixture data."""
    fixtures = os.path.join(os.path.dirname(__file__), "fixtures")

    # Codex uses YYYY/MM/DD structure
    date_dir = tmp_path / "2026" / "02" / "13"
    date_dir.mkdir(parents=True)

    shutil.copy(
        os.path.join(fixtures, "codex_session.jsonl"),
        date_dir / "rollout-2026-02-13T11-26-44-019c5841.jsonl",
    )

    return str(tmp_path)


def test_basic_extraction(codex_dir):
    """Test that we get the right number of turns and sessions."""
    turns, sessions = extract_codex(codex_dir, machine="test-host")

    assert len(sessions) == 1
    assert len(turns) == 2  # 2 completed turns (task_started -> task_complete)


def test_turn_fields_valid(codex_dir):
    """All turns pass schema validation."""
    turns, _ = extract_codex(codex_dir, machine="test-host")
    for t in turns:
        errors = validate_turn(t)
        assert errors == [], f"Turn {t['turn_number']} invalid: {errors}"


def test_session_fields_valid(codex_dir):
    """Session passes schema validation."""
    _, sessions = extract_codex(codex_dir, machine="test-host")
    for s in sessions:
        errors = validate_session(s)
        assert errors == [], f"Session {s['session_id']} invalid: {errors}"


def test_token_sums(codex_dir):
    """Session totals match summed turns."""
    turns, sessions = extract_codex(codex_dir, machine="test-host")
    s = sessions[0]

    assert s["total_input_tokens"] == sum(t["input_tokens"] for t in turns)
    assert s["total_output_tokens"] == sum(t["output_tokens"] for t in turns)
    assert s["total_cache_read_tokens"] == sum(t["cache_read_tokens"] for t in turns)
    assert s["total_cache_create_tokens"] == 0
    assert s["total_tokens"] == (
        s["total_input_tokens"] + s["total_output_tokens"]
        + s["total_cache_read_tokens"] + s["total_cache_create_tokens"]
    )


def test_reasoning_tokens(codex_dir):
    """reasoning_output_tokens should be populated from Codex data."""
    turns, sessions = extract_codex(codex_dir, machine="test-host")

    # Turn 1: reasoning delta = 427 - 0 = 427
    assert turns[0]["reasoning_output_tokens"] == 427
    # Turn 2: reasoning delta = 477 - 427 = 50
    assert turns[1]["reasoning_output_tokens"] == 50

    # reasoning is a subset of output, not additive
    for t in turns:
        assert t["reasoning_output_tokens"] <= t["output_tokens"]

    # Session total
    s = sessions[0]
    assert s["total_reasoning_output_tokens"] == 427 + 50


def test_input_decomposition(codex_dir):
    """input_tokens + cache_read_tokens should equal Codex's original input_tokens."""
    turns, _ = extract_codex(codex_dir, machine="test-host")

    # Turn 1: Codex input=17371, cached=16128 -> our input=1243, cache_read=16128
    assert turns[0]["input_tokens"] == 17371 - 16128  # = 1243
    assert turns[0]["cache_read_tokens"] == 16128
    assert turns[0]["input_tokens"] + turns[0]["cache_read_tokens"] == 17371

    # Turn 2: Codex input=5000, cached=4000 -> our input=1000, cache_read=4000
    assert turns[1]["input_tokens"] == 5000 - 4000  # = 1000
    assert turns[1]["cache_read_tokens"] == 4000
    assert turns[1]["input_tokens"] + turns[1]["cache_read_tokens"] == 5000


def test_model_family_gpt():
    """GPT models should be classified as 'gpt'."""
    assert model_family("gpt-5.3-codex") == "gpt"
    assert model_family("gpt-4o") == "gpt"
    assert model_family("gpt-4") == "gpt"


def test_model_family_in_turns(codex_dir):
    """All turns should be classified as gpt."""
    turns, _ = extract_codex(codex_dir, machine="test-host")
    assert all(t["model_family"] == "gpt" for t in turns)


def test_empty_sessions_dir(tmp_path):
    """Empty sessions dir returns empty results."""
    turns, sessions = extract_codex(str(tmp_path), machine="test")
    assert len(turns) == 0
    assert len(sessions) == 0


def test_nonexistent_dir():
    """Nonexistent dir returns empty results."""
    turns, sessions = extract_codex("/nonexistent/path", machine="test")
    assert len(turns) == 0
    assert len(sessions) == 0


def test_project_from_cwd():
    """Project name should be derived from session_meta cwd."""
    assert _project_from_cwd("c:\\Users\\testuser\\code\\myproject") == "myproject"
    assert _project_from_cwd("/home/user/project") == "project"
    assert _project_from_cwd("") == "(unknown)"


def test_session_title(codex_dir):
    """Session title should be derived from first user message."""
    _, sessions = extract_codex(codex_dir, machine="test")
    assert sessions[0]["title"] == "Build a REST API for the project"


def test_source_and_machine(codex_dir):
    """Verify source and machine fields."""
    turns, sessions = extract_codex(codex_dir, machine="my-workstation")
    assert all(t["source"] == "codex" for t in turns)
    assert all(t["machine"] == "my-workstation" for t in turns)
    assert sessions[0]["source"] == "codex"
    assert sessions[0]["machine"] == "my-workstation"


def test_session_id(codex_dir):
    """Session ID should come from session_meta."""
    _, sessions = extract_codex(codex_dir, machine="test")
    assert sessions[0]["session_id"] == "019c5841-5d19-71b1-b3d8-3d0f474d31e5"


def test_turn_numbering(codex_dir):
    """Turns should be numbered sequentially."""
    turns, _ = extract_codex(codex_dir, machine="test")
    turn_numbers = [t["turn_number"] for t in turns]
    assert turn_numbers == [1, 2]


def test_not_subagent(codex_dir):
    """Codex turns should have is_subagent=False."""
    turns, _ = extract_codex(codex_dir, machine="test")
    for t in turns:
        assert t["is_subagent"] is False
        assert t["subagent_id"] is None
