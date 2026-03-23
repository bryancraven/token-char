"""Tests for Cowork session parser."""

import json
import os
import shutil

import pytest

from token_char.schema import validate_session, validate_turn
from token_char.sources.cowork import extract_cowork


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture
def cowork_dir(tmp_path):
    shutil.copy(
        os.path.join(FIXTURES, "cowork_meta.json"),
        tmp_path / "local_abc123.json",
    )
    audit_dir = tmp_path / "local_abc123"
    audit_dir.mkdir()
    shutil.copy(
        os.path.join(FIXTURES, "cowork_audit.jsonl"),
        audit_dir / "audit.jsonl",
    )
    return str(tmp_path)


@pytest.fixture
def cowork_dedup_dir(tmp_path):
    shutil.copy(
        os.path.join(FIXTURES, "cowork_dedup_meta.json"),
        tmp_path / "local_modern123.json",
    )
    audit_dir = tmp_path / "local_modern123"
    audit_dir.mkdir()
    shutil.copy(
        os.path.join(FIXTURES, "cowork_dedup_audit.jsonl"),
        audit_dir / "audit.jsonl",
    )
    return str(tmp_path)


def test_basic_extraction_legacy(cowork_dir):
    turns, sessions = extract_cowork(cowork_dir, machine="test-host")
    assert len(sessions) == 1
    assert len(turns) == 4


def test_turn_fields_valid(cowork_dir):
    turns, _ = extract_cowork(cowork_dir, machine="test-host")
    for turn in turns:
        assert validate_turn(turn) == []


def test_session_fields_valid(cowork_dir):
    _, sessions = extract_cowork(cowork_dir, machine="test-host")
    for session in sessions:
        assert validate_session(session) == []


def test_user_turn_counts_legacy(cowork_dir):
    _, sessions = extract_cowork(cowork_dir, machine="test-host")
    assert sessions[0]["turns_user"] == 2


def test_token_sums_legacy(cowork_dir):
    _, sessions = extract_cowork(cowork_dir, machine="test-host")
    session = sessions[0]
    assert session["total_input_tokens"] == 4500
    assert session["total_output_tokens"] == 2500
    assert session["total_cache_read_tokens"] == 1500
    assert session["total_cache_create_tokens"] == 350
    assert session["total_tokens"] == 8850


def test_legacy_output_fields_are_unreliable(cowork_dir):
    turns, sessions = extract_cowork(cowork_dir, machine="test-host")
    assert all(t["output_tokens_reliable"] is False for t in turns)
    assert all(t["output_tokens_source"] == "assistant_snapshot" for t in turns)
    assert sessions[0]["total_output_tokens_reliable"] is False
    assert sessions[0]["total_output_tokens_source"] == "assistant_snapshot"


def test_source_machine_and_family(cowork_dir):
    turns, sessions = extract_cowork(cowork_dir, machine="my-mac")
    assert all(t["source"] == "cowork" for t in turns)
    assert all(t["machine"] == "my-mac" for t in turns)
    assert sessions[0]["source"] == "cowork"
    assert sessions[0]["machine"] == "my-mac"
    assert [t["model_family"] for t in turns] == ["opus", "opus", "unknown", "sonnet"]


def test_reasoning_output_tokens_zero(cowork_dir):
    turns, sessions = extract_cowork(cowork_dir, machine="test")
    assert all(t["reasoning_output_tokens"] == 0 for t in turns)
    assert sessions[0]["total_reasoning_output_tokens"] == 0


def test_skip_first_n_guard(cowork_dir):
    _, sessions = extract_cowork(cowork_dir, skip_first_n=1, machine="test")
    assert len(sessions) == 1


def test_duration(cowork_dir):
    _, sessions = extract_cowork(cowork_dir, machine="test")
    assert sessions[0]["duration_min"] == 60.0


def test_deduped_assistant_snapshots_and_result_output(cowork_dedup_dir):
    turns, sessions = extract_cowork(cowork_dedup_dir, machine="test-host")
    session = sessions[0]

    assert len(turns) == 3
    assert session["turns_assistant"] == 3
    assert session["subagent_turns"] == 1
    assert [t["turn_number"] for t in turns] == [1, 2, 3]

    assert session["total_input_tokens"] == 2700
    assert session["total_cache_read_tokens"] == 740
    assert session["total_cache_create_tokens"] == 160
    assert session["total_output_tokens"] == 1750
    assert session["total_output_tokens_reliable"] is True
    assert session["total_output_tokens_source"] == "result"
    assert session["total_cost_usd"] == pytest.approx(0.123456)
    assert session["total_tokens"] == 5350

    assert all(t["output_tokens_reliable"] is False for t in turns)
    assert turns[2]["is_subagent"] is True
    assert turns[2]["subagent_id"] == "tool2"


def test_missing_audit_file(tmp_path):
    meta = {
        "sessionId": "local_noaudit",
        "title": "No audit",
        "createdAt": 1700000000000,
    }
    with open(tmp_path / "local_noaudit.json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh)

    turns, sessions = extract_cowork(str(tmp_path), machine="test")
    assert turns == []
    assert sessions == []


def test_empty_audit_file(tmp_path):
    meta = {
        "sessionId": "local_empty",
        "title": "Empty session",
        "createdAt": 1700000000000,
        "lastActivityAt": 1700003600000,
    }
    with open(tmp_path / "local_empty.json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh)

    audit_dir = tmp_path / "local_empty"
    audit_dir.mkdir()
    (audit_dir / "audit.jsonl").write_text("", encoding="utf-8")

    turns, sessions = extract_cowork(str(tmp_path), machine="test")
    assert len(turns) == 0
    assert len(sessions) == 1
    assert sessions[0]["turns_assistant"] == 0
    assert sessions[0]["total_output_tokens_reliable"] is False


def test_malformed_jsonl(tmp_path):
    meta = {
        "sessionId": "local_bad",
        "title": "Bad lines",
        "createdAt": 1700000000000,
    }
    with open(tmp_path / "local_bad.json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh)

    audit_dir = tmp_path / "local_bad"
    audit_dir.mkdir()
    (audit_dir / "audit.jsonl").write_text(
        "not valid json\n"
        '{"type": "assistant", "message": {"model": "claude-opus-4-5-20250514", '
        '"usage": {"input_tokens": 100, "output_tokens": 50, '
        '"cache_read_input_tokens": 10, "cache_creation_input_tokens": 5}, '
        '"content": []}}\n',
        encoding="utf-8",
    )

    turns, sessions = extract_cowork(str(tmp_path), machine="test")
    assert len(turns) == 1
    assert turns[0]["input_tokens"] == 100
    assert sessions[0]["turns_assistant"] == 1
