"""
Unit tests for modules/terminal.py

Covers state load/save (including MAX_HISTORY truncation and corrupt-file
fallback) and the line-wrapping helper used when rendering command output.
"""

import sys
import os
import json

import pytest
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import modules.terminal as terminal


@pytest.fixture
def output_path(tmp_path):
    return str(tmp_path / "terminal.bmp")


@pytest.fixture
def state_path(tmp_path, monkeypatch):
    path = str(tmp_path / "terminal_state.json")
    monkeypatch.setattr(terminal, "STATE_PATH", path)
    return path


class TestLoadState:
    def test_missing_file_returns_empty_history(self, state_path):
        assert terminal.load_state() == {"history": []}

    def test_loads_existing_state(self, state_path):
        with open(state_path, "w") as f:
            json.dump({"history": [{"command": "ls"}]}, f)
        assert terminal.load_state() == {"history": [{"command": "ls"}]}

    def test_corrupt_file_returns_empty_history(self, state_path):
        with open(state_path, "w") as f:
            f.write("{not valid json")
        assert terminal.load_state() == {"history": []}


class TestSaveEntry:
    def test_save_entry_appends_to_history(self, state_path):
        terminal.save_entry("ls -la", "file1\nfile2", 0)
        state = terminal.load_state()
        assert len(state["history"]) == 1
        entry = state["history"][0]
        assert entry["command"] == "ls -la"
        assert entry["output"] == "file1\nfile2"
        assert entry["exit_code"] == 0
        assert "timestamp" in entry

    def test_save_entry_keeps_only_last_max_history(self, state_path):
        for i in range(terminal.MAX_HISTORY + 5):
            terminal.save_entry(f"cmd{i}", "out", 0)
        state = terminal.load_state()
        assert len(state["history"]) == terminal.MAX_HISTORY
        # The oldest entries should have been dropped; last one is the most recent.
        assert state["history"][-1]["command"] == f"cmd{terminal.MAX_HISTORY + 4}"
        assert state["history"][0]["command"] == "cmd5"


class TestWrapLine:
    def _draw(self):
        img = Image.new("RGB", (800, 480))
        return ImageDraw.Draw(img)

    def test_empty_text_returns_single_empty_line(self):
        draw = self._draw()
        font = terminal._font(14)
        assert terminal._wrap_line("", font, draw, 700) == [""]

    def test_short_line_not_wrapped(self):
        draw = self._draw()
        font = terminal._font(14)
        lines = terminal._wrap_line("short line", font, draw, 700)
        assert lines == ["short line"]

    def test_long_line_wraps_into_multiple_lines(self):
        draw = self._draw()
        font = terminal._font(14)
        text = " ".join(["word"] * 100)
        lines = terminal._wrap_line(text, font, draw, 200)
        assert len(lines) > 1
        # Reassembling should reproduce all the words (order preserved).
        assert " ".join(lines).split() == text.split()

    def test_single_word_exceeding_max_width_not_split(self):
        """A single word longer than max_width is emitted whole (character-level
        wrapping isn't attempted) rather than dropped or raising."""
        draw = self._draw()
        font = terminal._font(14)
        long_word = "x" * 500
        lines = terminal._wrap_line(long_word, font, draw, 50)
        assert lines == [long_word]


class TestRender:
    def test_empty_history_shows_placeholder(self, output_path):
        terminal._render({"history": []}, output_path)
        assert os.path.exists(output_path)
        img = Image.open(output_path)
        assert img.size == (800, 480)

    def test_render_with_history_produces_correct_size(self, output_path):
        state = {
            "history": [
                {"command": "ls -la", "output": "file1\nfile2", "exit_code": 0, "timestamp": "12:00:00"},
                {"command": "false", "output": "", "exit_code": 1, "timestamp": "12:00:05"},
            ]
        }
        terminal._render(state, output_path)
        img = Image.open(output_path)
        assert img.size == (800, 480)

    def test_custom_dimensions_respected(self, output_path):
        terminal._render({"history": []}, output_path, width=400, height=200)
        img = Image.open(output_path)
        assert img.size == (400, 200)

    def test_creates_parent_directories(self, tmp_path):
        nested = str(tmp_path / "a" / "b" / "terminal.bmp")
        terminal._render({"history": []}, nested)
        assert os.path.exists(nested)

    def test_history_truncated_to_lines_that_fit(self, output_path):
        """With a tiny canvas, only the most recent lines should render —
        _render must not raise even when history overflows the visible area."""
        state = {
            "history": [
                {"command": f"cmd{i}", "output": f"output line {i}", "exit_code": 0, "timestamp": "12:00:00"}
                for i in range(terminal.MAX_HISTORY)
            ]
        }
        terminal._render(state, output_path, width=800, height=60)
        img = Image.open(output_path)
        assert img.size == (800, 60)


class TestGenerate:
    def test_generate_uses_default_output_path(self, tmp_path, monkeypatch, state_path):
        monkeypatch.chdir(tmp_path)
        config = {"terminal": {}, "width": 800, "height": 480}
        result = terminal.generate(config)
        assert result == "images/terminal_display.bmp"
        assert os.path.exists(result)

    def test_generate_uses_configured_output_path(self, tmp_path, state_path):
        out = str(tmp_path / "custom" / "term.bmp")
        config = {"terminal": {"output_path": out}, "width": 800, "height": 480}
        result = terminal.generate(config)
        assert result == out
        assert os.path.exists(out)

    def test_generate_reads_saved_history(self, tmp_path, state_path):
        terminal.save_entry("echo hi", "hi", 0)
        out = str(tmp_path / "term.bmp")
        config = {"terminal": {"output_path": out}, "width": 800, "height": 480}
        result = terminal.generate(config)
        assert os.path.exists(result)
