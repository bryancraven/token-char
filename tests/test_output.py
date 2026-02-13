"""Tests for output writers."""

import csv
import json
import os
import shutil

import pytest

from token_char.sources.cowork import extract_cowork
from token_char.sources.claude_code import extract_claude_code
from token_char.output import write_json, write_csv, write_jsonl
from token_char.schema import TURN_FIELDS, SESSION_FIELDS


@pytest.fixture
def cowork_dir(tmp_path):
    fixtures = os.path.join(os.path.dirname(__file__), "fixtures")
    shutil.copy(
        os.path.join(fixtures, "cowork_meta.json"),
        tmp_path / "local_abc123.json",
    )
    audit_dir = tmp_path / "local_abc123"
    audit_dir.mkdir()
    shutil.copy(
        os.path.join(fixtures, "cowork_audit.jsonl"),
        audit_dir / "audit.jsonl",
    )
    return str(tmp_path)


@pytest.fixture
def sample_data(cowork_dir):
    turns, sessions = extract_cowork(cowork_dir, machine="test-host")
    return turns, sessions


def test_json_output(sample_data, tmp_path):
    """JSON output should be valid and contain required envelope fields."""
    turns, sessions = sample_data
    out_path = str(tmp_path / "out.json")

    write_json(turns, sessions, out_path, "test-host", ["cowork"], "0.1.0")

    with open(out_path) as f:
        data = json.load(f)

    assert data["token_char_version"] == "0.1.0"
    assert data["machine"] == "test-host"
    assert "extracted_at" in data
    assert data["sources"] == ["cowork"]
    assert len(data["turns"]) == len(turns)
    assert len(data["sessions"]) == len(sessions)

    # Verify turn fields
    for t in data["turns"]:
        for field in TURN_FIELDS:
            assert field in t, f"Missing field {field} in turn"

    # Verify session fields
    for s in data["sessions"]:
        for field in SESSION_FIELDS:
            assert field in s, f"Missing field {field} in session"


def test_csv_output(sample_data, tmp_path):
    """CSV output should produce valid files with correct headers."""
    turns, sessions = sample_data
    out_dir = str(tmp_path / "csv_out")
    os.makedirs(out_dir)

    write_csv(turns, sessions, out_dir)

    turns_path = os.path.join(out_dir, "turns.csv")
    sessions_path = os.path.join(out_dir, "sessions.csv")

    assert os.path.isfile(turns_path)
    assert os.path.isfile(sessions_path)

    # Verify turns CSV
    with open(turns_path) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        assert len(rows) == len(turns)
        assert set(reader.fieldnames) == set(TURN_FIELDS)

    # Verify sessions CSV
    with open(sessions_path) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        assert len(rows) == len(sessions)
        assert set(reader.fieldnames) == set(SESSION_FIELDS)


def test_jsonl_output(sample_data, tmp_path):
    """JSONL output should have one record per line with _record_type."""
    turns, sessions = sample_data
    out_path = str(tmp_path / "out.jsonl")

    write_jsonl(turns, sessions, out_path)

    with open(out_path) as f:
        lines = [l for l in f.read().strip().split("\n") if l]

    assert len(lines) == len(turns) + len(sessions)

    turn_lines = []
    session_lines = []
    for line in lines:
        rec = json.loads(line)
        assert "_record_type" in rec
        if rec["_record_type"] == "turn":
            turn_lines.append(rec)
        elif rec["_record_type"] == "session":
            session_lines.append(rec)

    assert len(turn_lines) == len(turns)
    assert len(session_lines) == len(sessions)


def test_json_roundtrip_token_totals(sample_data, tmp_path):
    """Token totals should be preserved through JSON serialization."""
    turns, sessions = sample_data
    out_path = str(tmp_path / "rt.json")

    write_json(turns, sessions, out_path, "test", ["cowork"], "0.1.0")

    with open(out_path) as f:
        data = json.load(f)

    for orig, loaded in zip(turns, data["turns"]):
        assert loaded["input_tokens"] == orig["input_tokens"]
        assert loaded["output_tokens"] == orig["output_tokens"]
        assert loaded["cache_read_tokens"] == orig["cache_read_tokens"]
        assert loaded["cache_create_tokens"] == orig["cache_create_tokens"]
        assert loaded["total_tokens"] == orig["total_tokens"]


def test_json_output_subagent_fields(sample_data, tmp_path):
    """JSON output should include is_subagent and subagent_id in turns, subagent_turns in sessions."""
    turns, sessions = sample_data
    out_path = str(tmp_path / "out_sa.json")

    write_json(turns, sessions, out_path, "test-host", ["cowork"], "0.1.0")

    with open(out_path) as f:
        data = json.load(f)

    for t in data["turns"]:
        assert "is_subagent" in t, "Missing is_subagent in turn"
        assert "subagent_id" in t, "Missing subagent_id in turn"

    for s in data["sessions"]:
        assert "subagent_turns" in s, "Missing subagent_turns in session"


def test_json_output_reasoning_fields(sample_data, tmp_path):
    """JSON output should include reasoning_output_tokens in turns and total_reasoning_output_tokens in sessions."""
    turns, sessions = sample_data
    out_path = str(tmp_path / "out_reason.json")

    write_json(turns, sessions, out_path, "test-host", ["cowork"], "0.1.0")

    with open(out_path) as f:
        data = json.load(f)

    for t in data["turns"]:
        assert "reasoning_output_tokens" in t, "Missing reasoning_output_tokens in turn"

    for s in data["sessions"]:
        assert "total_reasoning_output_tokens" in s, "Missing total_reasoning_output_tokens in session"


def test_csv_output_subagent_fields(sample_data, tmp_path):
    """CSV output should include subagent columns."""
    turns, sessions = sample_data
    out_dir = str(tmp_path / "csv_sa")
    os.makedirs(out_dir)

    write_csv(turns, sessions, out_dir)

    with open(os.path.join(out_dir, "turns.csv")) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        assert "is_subagent" in reader.fieldnames
        assert "subagent_id" in reader.fieldnames

    with open(os.path.join(out_dir, "sessions.csv")) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        assert "subagent_turns" in reader.fieldnames
