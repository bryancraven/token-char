"""Tests for stats module."""

import pytest

from token_char.stats import percentile, percentile_stats, compute_source_stats, fmt_k


class TestPercentile:
    def test_single_value(self):
        assert percentile([42], 50) == 42
        assert percentile([42], 99) == 42

    def test_two_values(self):
        assert percentile([10, 20], 50) == 15.0
        assert percentile([10, 20], 0) == 10
        assert percentile([10, 20], 100) == 20

    def test_known_values(self):
        vals = list(range(1, 101))  # 1..100
        assert percentile(vals, 50) == 50.5
        assert abs(percentile(vals, 90) - 90.1) < 1e-9
        assert abs(percentile(vals, 99) - 99.01) < 1e-9

    def test_interpolation(self):
        vals = [10, 20, 30, 40, 50]
        assert percentile(vals, 25) == 20.0
        assert percentile(vals, 75) == 40.0


class TestPercentileStats:
    def test_empty(self):
        result = percentile_stats([])
        assert result["n"] == 0
        assert result["sum"] == 0
        assert result["median"] == 0
        assert result["mean"] == 0

    def test_single(self):
        result = percentile_stats([100])
        assert result["n"] == 1
        assert result["sum"] == 100
        assert result["median"] == 100
        assert result["mean"] == 100.0
        assert result["max"] == 100

    def test_known_distribution(self):
        vals = [10, 20, 30, 40, 50]
        result = percentile_stats(vals)
        assert result["n"] == 5
        assert result["sum"] == 150
        assert result["median"] == 30
        assert result["mean"] == 30.0
        assert result["max"] == 50

    def test_unsorted_input(self):
        """percentile_stats should sort internally."""
        result = percentile_stats([50, 10, 30, 40, 20])
        assert result["median"] == 30
        assert result["max"] == 50


class TestFmtK:
    def test_millions(self):
        assert fmt_k(1_234_567) == "1.2M"
        assert fmt_k(5_500_000) == "5.5M"

    def test_thousands(self):
        assert fmt_k(45_000) == "45.0K"
        assert fmt_k(1_200) == "1.2K"

    def test_small(self):
        assert fmt_k(800) == "800"
        assert fmt_k(0) == "0"
        assert fmt_k(7) == "7"

    def test_none(self):
        assert fmt_k(None) == "-"

    def test_boundary(self):
        assert fmt_k(1_000) == "1.0K"
        assert fmt_k(1_000_000) == "1.0M"
        assert fmt_k(999) == "999"


class TestComputeSourceStats:
    @pytest.fixture
    def sample_turns(self):
        """Minimal turn dicts for testing."""
        return [
            {
                "source": "cowork",
                "input_tokens": 1000,
                "output_tokens": 5,
                "cache_read_tokens": 200,
                "cache_create_tokens": 100,
                "reasoning_output_tokens": 0,
                "total_tokens": 1305,
                "is_subagent": False,
            },
            {
                "source": "cowork",
                "input_tokens": 1500,
                "output_tokens": 800,
                "cache_read_tokens": 500,
                "cache_create_tokens": 50,
                "reasoning_output_tokens": 0,
                "total_tokens": 2850,
                "is_subagent": False,
            },
            {
                "source": "cowork",
                "input_tokens": 2000,
                "output_tokens": 1200,
                "cache_read_tokens": 800,
                "cache_create_tokens": 200,
                "reasoning_output_tokens": 0,
                "total_tokens": 4200,
                "is_subagent": False,
            },
        ]

    @pytest.fixture
    def sample_sessions(self):
        return [
            {
                "source": "cowork",
                "project": "test-project",
                "session_id": "s1",
                "title": "Test session",
                "model": "claude-sonnet-4-5-20250514",
                "created_at": "2026-02-01T00:00:00+00:00",
                "turns_user": 2,
                "turns_assistant": 3,
                "total_input_tokens": 4500,
                "total_output_tokens": 2005,
                "total_cache_read_tokens": 1500,
                "total_cache_create_tokens": 350,
                "total_tokens": 8355,
                "subagent_turns": 0,
            },
        ]

    def test_basic_structure(self, sample_turns, sample_sessions):
        result = compute_source_stats(sample_turns, sample_sessions)
        assert result is not None
        assert result["source"] == "cowork"
        assert result["counts"]["sessions"] == 1
        assert result["counts"]["turns"] == 3

    def test_composition(self, sample_turns, sample_sessions):
        result = compute_source_stats(sample_turns, sample_sessions)
        comp = result["composition"]
        # All should sum to ~100%
        total_pct = comp["cache_read"] + comp["cache_create"] + comp["input"] + comp["output"]
        assert abs(total_pct - 100.0) < 0.1

    def test_turn_profile(self, sample_turns, sample_sessions):
        result = compute_source_stats(sample_turns, sample_sessions)
        tp = result["turn_profile"]
        # Turn 1 has output_tokens=5 (tool-use), turns 2,3 are substantive
        assert tp["tool_use"] == 1
        assert tp["substantive"] == 2

    def test_empty_data(self):
        result = compute_source_stats([], [])
        assert result is None

    def test_date_range(self, sample_turns, sample_sessions):
        result = compute_source_stats(sample_turns, sample_sessions)
        assert result["counts"]["date_start"] == "2026-02-01"
        assert result["counts"]["date_end"] == "2026-02-01"

    def test_subagent_count(self, sample_sessions):
        turns = [
            {
                "source": "claude_code",
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_tokens": 0,
                "cache_create_tokens": 0,
                "reasoning_output_tokens": 0,
                "total_tokens": 150,
                "is_subagent": True,
            },
            {
                "source": "claude_code",
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_tokens": 0,
                "cache_create_tokens": 0,
                "reasoning_output_tokens": 0,
                "total_tokens": 150,
                "is_subagent": False,
            },
        ]
        sessions = [{**sample_sessions[0], "source": "claude_code"}]
        result = compute_source_stats(turns, sessions)
        assert result["subagent_turns"] == 1
