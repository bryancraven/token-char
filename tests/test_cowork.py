"""Tests for Cowork session parser."""

import json
import os
import shutil
import tempfile

import pytest

from token_char.sources.cowork import extract_cowork
from token_char.schema import validate_turn, validate_session


@pytest.fixture
def cowork_dir(tmp_path):
    """Set up a fake Cowork project directory with fixture data."""
    fixtures = os.path.join(os.path.dirname(__file__), "fixtures")

    # Copy metadata as local_abc123.json
    shutil.copy(
        os.path.join(fixtures, "cowork_meta.json"),
        tmp_path / "local_abc123.json",
    )

    # Create audit dir and copy audit log
    audit_dir = tmp_path / "local_abc123"
    audit_dir.mkdir()
    shutil.copy(
        os.path.join(fixtures, "cowork_audit.jsonl"),
        audit_dir / "audit.jsonl",
    )

    return str(tmp_path)


def test_basic_extraction(cowork_dir):
    """Test that we get the right number of turns and sessions."""
    turns, sessions = extract_cowork(cowork_dir, machine="test-host")

    assert len(sessions) == 1
    assert len(turns) == 4  # 4 assistant turns (including <synthetic>)


def test_turn_fields_valid(cowork_dir):
    """All turns pass schema validation."""
    turns, _ = extract_cowork(cowork_dir, machine="test-host")
    for t in turns:
        errors = validate_turn(t)
        assert errors == [], f"Turn {t['turn_number']} invalid: {errors}"


def test_session_fields_valid(cowork_dir):
    """Session passes schema validation."""
    _, sessions = extract_cowork(cowork_dir, machine="test-host")
    for s in sessions:
        errors = validate_session(s)
        assert errors == [], f"Session {s['session_id']} invalid: {errors}"


def test_user_turn_counts(cowork_dir):
    """Tool_result callbacks should not count as user turns."""
    _, sessions = extract_cowork(cowork_dir, machine="test-host")
    s = sessions[0]
    # 2 genuine user messages ("Hello..." and "Thanks..."), 1 tool_result skipped
    assert s["turns_user"] == 2


def test_token_sums(cowork_dir):
    """Verify token totals are summed correctly."""
    turns, sessions = extract_cowork(cowork_dir, machine="test-host")
    s = sessions[0]

    # Sum from non-synthetic turns: turn1 + turn2 + turn3
    expected_input = 1000 + 1500 + 2000  # = 4500 (plus 0 from synthetic)
    expected_output = 500 + 800 + 1200   # = 2500
    expected_cr = 200 + 500 + 800        # = 1500
    expected_cc = 100 + 50 + 200         # = 350

    assert s["total_input_tokens"] == expected_input
    assert s["total_output_tokens"] == expected_output
    assert s["total_cache_read_tokens"] == expected_cr
    assert s["total_cache_create_tokens"] == expected_cc
    assert s["total_tokens"] == expected_input + expected_output + expected_cr + expected_cc


def test_source_and_machine(cowork_dir):
    """Verify source and machine fields are set."""
    turns, sessions = extract_cowork(cowork_dir, machine="my-mac")
    assert all(t["source"] == "cowork" for t in turns)
    assert all(t["machine"] == "my-mac" for t in turns)
    assert sessions[0]["source"] == "cowork"
    assert sessions[0]["machine"] == "my-mac"


def test_model_family(cowork_dir):
    """Verify model_family classification."""
    turns, _ = extract_cowork(cowork_dir, machine="test")
    families = [t["model_family"] for t in turns]
    # turn 1,2 = opus, turn 3 = unknown (<synthetic>), turn 4 = sonnet
    assert families[0] == "opus"
    assert families[1] == "opus"
    assert families[2] == "unknown"  # <synthetic>
    assert families[3] == "sonnet"


def test_skip_first_n(cowork_dir):
    """skip_first_n=1 with only 1 session keeps it (guard: len > skip)."""
    turns, sessions = extract_cowork(cowork_dir, skip_first_n=1, machine="test")
    # Only 1 session — not skipped because len(1) > skip(1) is False
    assert len(sessions) == 1

    # skip_first_n=0 also keeps it
    turns2, sessions2 = extract_cowork(cowork_dir, skip_first_n=0, machine="test")
    assert len(sessions2) == 1


def test_duration(cowork_dir):
    """Session duration should be computed from metadata timestamps."""
    _, sessions = extract_cowork(cowork_dir, machine="test")
    s = sessions[0]
    # (1707786000000 - 1707782400000) / 60000 = 60.0 minutes
    assert s["duration_min"] == 60.0


def test_missing_audit_file(tmp_path):
    """Sessions with no audit.jsonl are skipped."""
    meta = {
        "sessionId": "local_noaudit",
        "title": "No audit",
        "createdAt": 1700000000000,
    }
    with open(tmp_path / "local_noaudit.json", "w") as f:
        json.dump(meta, f)

    turns, sessions = extract_cowork(str(tmp_path), machine="test")
    assert len(sessions) == 0
    assert len(turns) == 0


def test_empty_audit_file(tmp_path):
    """Empty audit.jsonl produces a session with zero turns."""
    meta = {
        "sessionId": "local_empty",
        "title": "Empty session",
        "createdAt": 1700000000000,
        "lastActivityAt": 1700003600000,
    }
    with open(tmp_path / "local_empty.json", "w") as f:
        json.dump(meta, f)

    audit_dir = tmp_path / "local_empty"
    audit_dir.mkdir()
    (audit_dir / "audit.jsonl").write_text("")

    turns, sessions = extract_cowork(str(tmp_path), machine="test")
    assert len(sessions) == 1
    assert sessions[0]["turns_assistant"] == 0
    assert len(turns) == 0


def test_malformed_jsonl(tmp_path):
    """Malformed JSONL lines are skipped silently."""
    meta = {
        "sessionId": "local_bad",
        "title": "Bad lines",
        "createdAt": 1700000000000,
    }
    with open(tmp_path / "local_bad.json", "w") as f:
        json.dump(meta, f)

    audit_dir = tmp_path / "local_bad"
    audit_dir.mkdir()
    (audit_dir / "audit.jsonl").write_text(
        "not valid json\n"
        '{"type": "assistant", "message": {"model": "claude-opus-4-5-20250514", '
        '"usage": {"input_tokens": 100, "output_tokens": 50, '
        '"cache_read_input_tokens": 10, "cache_creation_input_tokens": 5}, '
        '"content": []}}\n'
        "\n"  # empty line
    )

    turns, sessions = extract_cowork(str(tmp_path), machine="test")
    assert len(turns) == 1
    assert turns[0]["input_tokens"] == 100
