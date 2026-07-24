"""Tests for core/stream.py — ConsoleStream and normalize_transcript."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from core.stream import ConsoleStream, normalize_transcript, strip_ansi


class TestStripAnsi:
    def test_no_ansi(self):
        assert strip_ansi("hello world") == "hello world"

    def test_color_code(self):
        assert strip_ansi("\x1b[32mgreen\x1b[0m") == "green"

    def test_complex_escape(self):
        assert strip_ansi("\x1b[1;31mred bold\x1b[0m text") == "red bold text"

    def test_empty(self):
        assert strip_ansi("") == ""


class TestConsoleStream:
    def test_simple_newline(self):
        cs = ConsoleStream()
        cs.feed("hello\nworld\n")
        assert cs._lines == ["hello", "world"]

    def test_carriage_return_replaces_progress(self):
        cs = ConsoleStream()
        cs.feed("50% 10/20\r100% 20/20\n")
        # Only the last value should remain (progress replaced)
        assert len(cs._lines) == 1
        assert "100%" in cs._lines[0]

    def test_ansi_stripped_in_storage(self):
        cs = ConsoleStream()
        cs.feed("\x1b[32mgreen\x1b[0m\n")
        assert cs._lines == ["green"]

    def test_backspace(self):
        cs = ConsoleStream()
        cs.feed("abc\b\n")
        assert cs._lines == ["ab"]

    def test_blank_lines_ignored(self):
        cs = ConsoleStream()
        cs.feed("hello\n\n\nworld\n")
        assert cs._lines == ["hello", "world"]

    def test_transcript(self):
        cs = ConsoleStream()
        cs.feed("line1\nline2\n")
        assert cs.transcript() == "line1\nline2"

    def test_display_lines_have_newlines(self):
        cs = ConsoleStream()
        cs.feed("alpha\nbeta\n")
        lines = cs.display_lines()
        assert all(ln.endswith("\n") for ln in lines)

    def test_flush_partial_buffer(self):
        cs = ConsoleStream()
        cs.feed("partial")  # no newline at end
        assert cs.transcript() == "partial"


class TestNormalizeTranscript:
    def test_strips_ansi(self):
        result = normalize_transcript("\x1b[32mok\x1b[0m")
        assert result == "ok"

    def test_empty(self):
        assert normalize_transcript("") == ""

    def test_multiline(self):
        result = normalize_transcript("line1\nline2\n")
        assert "line1" in result and "line2" in result
