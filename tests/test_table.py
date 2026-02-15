"""Tests for table formatter."""

import io
import os
import shutil

import pytest

from token_char.sources.cowork import extract_cowork
from token_char.sources.codex import extract_codex
from token_char.table import write_table, _detect_charset, UNICODE_CHARS, ASCII_CHARS


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
def codex_dir(tmp_path):
    fixtures = os.path.join(os.path.dirname(__file__), "fixtures")
    date_dir = tmp_path / "2026" / "02" / "13"
    date_dir.mkdir(parents=True)
    shutil.copy(
        os.path.join(fixtures, "codex_session.jsonl"),
        date_dir / "rollout-2026-02-13T11-26-44-019c5841.jsonl",
    )
    return str(tmp_path)


class TestWriteTable:
    def test_cowork_output_has_sections(self, cowork_dir):
        turns, sessions = extract_cowork(cowork_dir, machine="test")
        buf = io.StringIO()
        write_table(turns, sessions, file=buf)
        output = buf.getvalue()

        assert "Claude Desktop (Cowork)" in output
        assert "Tokens/Turn (assistant)" in output
        assert "Median" in output
        assert "Cache Read" in output
        assert "Cache Create" in output
        assert "Composition:" in output
        assert "Cache hit ratio:" in output
        assert "GRAND TOTAL" in output

    def test_codex_output_has_sections(self, codex_dir):
        turns, sessions = extract_codex(codex_dir, machine="test")
        buf = io.StringIO()
        write_table(turns, sessions, file=buf)
        output = buf.getvalue()

        assert "OpenAI Codex" in output
        assert "API call" in output
        assert "reasoning" in output
        assert "not reported by OpenAI" in output
        assert "GRAND TOTAL" in output

    def test_codex_no_turn_profile(self, codex_dir):
        """Codex blocks should not show turn profile."""
        turns, sessions = extract_codex(codex_dir, machine="test")
        buf = io.StringIO()
        write_table(turns, sessions, file=buf)
        output = buf.getvalue()

        assert "Turn profile:" not in output
        assert "Substantive output" not in output

    def test_cowork_has_turn_profile(self, cowork_dir):
        turns, sessions = extract_cowork(cowork_dir, machine="test")
        buf = io.StringIO()
        write_table(turns, sessions, file=buf)
        output = buf.getvalue()

        assert "Turn profile:" in output

    def test_empty_data(self):
        buf = io.StringIO()
        write_table([], [], file=buf)
        assert "No data found" in buf.getvalue()

    def test_session_detail(self, cowork_dir):
        turns, sessions = extract_cowork(cowork_dir, machine="test")
        buf = io.StringIO()
        write_table(turns, sessions, detail="sessions", file=buf)
        output = buf.getvalue()

        assert "Sessions" in output
        # Session title from fixture (truncated to 22 chars with ...)
        assert "Test session for co" in output

    def test_multi_source(self, cowork_dir, codex_dir):
        """Both sources should appear when combined."""
        t1, s1 = extract_cowork(cowork_dir, machine="test")
        t2, s2 = extract_codex(codex_dir, machine="test")
        buf = io.StringIO()
        write_table(t1 + t2, s1 + s2, file=buf)
        output = buf.getvalue()

        assert "Claude Desktop (Cowork)" in output
        assert "OpenAI Codex" in output
        assert "2 sources" in output

    def test_grand_total_tokens(self, cowork_dir):
        turns, sessions = extract_cowork(cowork_dir, machine="test")
        buf = io.StringIO()
        write_table(turns, sessions, file=buf)
        output = buf.getvalue()

        # Grand total should show 1 source
        assert "1 sources" in output or "1 source" in output


class TestASCIIMode:
    """Tests for ASCII fallback charset and auto-detection."""

    UNICODE_SPECIALS = {"\u2550", "\u2500", "\u2502", "\u2514", "\u2020", "\u2264"}

    def test_ascii_flag_forces_ascii(self, cowork_dir):
        """ascii=True should produce output with no Unicode box-drawing chars."""
        turns, sessions = extract_cowork(cowork_dir, machine="test")
        buf = io.StringIO()
        write_table(turns, sessions, file=buf, ascii=True)
        output = buf.getvalue()

        for ch in self.UNICODE_SPECIALS:
            assert ch not in output, f"Unicode char {ch!r} found in ASCII output"

        # Verify ASCII equivalents are present
        assert "=" in output  # double_line
        assert "-" in output  # thin_line
        assert "|" in output  # vertical

    def test_auto_detect_utf8(self):
        """A file with encoding='utf-8' should get UNICODE_CHARS."""

        class UTF8File:
            encoding = "utf-8"

        result = _detect_charset(UTF8File())
        assert result is UNICODE_CHARS

    def test_auto_detect_ascii_fallback(self):
        """A file with encoding='cp1252' should fall back to ASCII_CHARS."""

        class FakeFile:
            encoding = "cp1252"

        result = _detect_charset(FakeFile())
        assert result is ASCII_CHARS

    def test_ascii_cowork_has_all_sections(self, cowork_dir):
        """ASCII mode should have all the same sections as Unicode."""
        turns, sessions = extract_cowork(cowork_dir, machine="test")
        buf = io.StringIO()
        write_table(turns, sessions, file=buf, ascii=True)
        output = buf.getvalue()

        assert "Claude Desktop (Cowork)" in output
        assert "Tokens/Turn (assistant)" in output
        assert "Cache Read" in output
        assert "Cache Create" in output
        assert "Composition:" in output
        assert "Cache hit ratio:" in output
        assert "GRAND TOTAL" in output

    def test_ascii_codex_has_all_sections(self, codex_dir):
        """ASCII mode should work for Codex output too."""
        turns, sessions = extract_codex(codex_dir, machine="test")
        buf = io.StringIO()
        write_table(turns, sessions, file=buf, ascii=True)
        output = buf.getvalue()

        for ch in self.UNICODE_SPECIALS:
            assert ch not in output, f"Unicode char {ch!r} found in ASCII output"

        assert "OpenAI Codex" in output
        assert "API call" in output
        assert "GRAND TOTAL" in output

    def test_detect_charset_no_encoding_attr(self):
        """A file-like object with no encoding attr should fall back to ASCII."""

        class BareFile:
            pass

        result = _detect_charset(BareFile())
        assert result is ASCII_CHARS
