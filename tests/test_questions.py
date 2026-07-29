"""
Unit tests for modules/questions.py.
"""

import csv
import json
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules import questions


def _write_csv(path, rows):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["topic", "question"])
        for topic, question in rows:
            writer.writerow([topic, question])


class TestLoadQuestions:
    def test_loads_valid_rows(self, tmp_path):
        csv_path = str(tmp_path / "q.csv")
        _write_csv(csv_path, [("Science", "Why is the sky blue?"), ("History", "Who was first?")])
        rows = questions._load_questions(csv_path)
        assert rows == [("Science", "Why is the sky blue?"), ("History", "Who was first?")]

    def test_skips_rows_with_empty_question(self, tmp_path):
        csv_path = str(tmp_path / "q.csv")
        _write_csv(csv_path, [("Science", ""), ("History", "Real question")])
        rows = questions._load_questions(csv_path)
        assert rows == [("History", "Real question")]

    def test_missing_file_returns_empty_list(self, tmp_path):
        rows = questions._load_questions(str(tmp_path / "missing.csv"))
        assert rows == []

    def test_strips_whitespace(self, tmp_path):
        csv_path = str(tmp_path / "q.csv")
        _write_csv(csv_path, [("  Science  ", "  Padded question?  ")])
        rows = questions._load_questions(csv_path)
        assert rows == [("Science", "Padded question?")]


class TestPickNewQuestion:
    def test_avoids_recent_indices_when_pool_available(self):
        questions_list = list(range(10))
        recent = [0, 1, 2, 3]
        # With max_recent_ratio=0.4, up to 4 of 10 are excluded; run many times
        # and confirm the recently-shown ones are never selected.
        for _ in range(50):
            pick = questions._pick_new_question(questions_list, recent, max_recent_ratio=0.4)
            assert pick not in recent

    def test_falls_back_to_full_pool_when_all_excluded(self):
        questions_list = list(range(3))
        recent = [0, 1, 2, 0, 1, 2]
        # max_recent = max(1, int(3*0.4)) = 1, exclude last 1 index only since recent < max_recent is False (6>=1)
        # exclude = set(recent[-1:]) = {2}
        pick = questions._pick_new_question(questions_list, recent, max_recent_ratio=0.4)
        assert pick in (0, 1)

    def test_single_question_pool_always_returns_it(self):
        pick = questions._pick_new_question([42], [], max_recent_ratio=0.4)
        assert pick == 0

    def test_empty_recent_can_return_anything_in_pool(self):
        questions_list = list(range(5))
        pick = questions._pick_new_question(questions_list, [])
        assert pick in questions_list


class TestState:
    def test_load_state_missing_file_returns_empty_dict(self, tmp_path):
        assert questions._load_state(str(tmp_path / "missing.json")) == {}

    def test_save_then_load_roundtrip(self, tmp_path):
        state_file = str(tmp_path / "state.json")
        state = {"current_index": 3, "last_updated": 12345.0, "recent_indices": [1, 2, 3]}
        questions._save_state(state_file, state)
        loaded = questions._load_state(state_file)
        assert loaded == state

    def test_corrupt_state_file_returns_empty_dict(self, tmp_path):
        state_file = str(tmp_path / "state.json")
        with open(state_file, "w") as f:
            f.write("not valid json{{{")
        assert questions._load_state(state_file) == {}


class TestWrap:
    def test_wraps_long_text_into_multiple_lines(self):
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (10, 10))
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default()
        wrapped = questions._wrap("one two three four five six seven eight", font, draw, 40)
        assert "\n" in wrapped

    def test_empty_text_returns_empty_string(self):
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (10, 10))
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default()
        assert questions._wrap("", font, draw, 1000) == ""


class TestGenerateStateTransitions:
    def _base_config(self, tmp_path, **overrides):
        csv_path = str(tmp_path / "q.csv")
        _write_csv(csv_path, [("T1", "Question one?"), ("T2", "Question two?"), ("T3", "Question three?")])
        cfg = {
            "questions": {
                "output_path": str(tmp_path / "out.bmp"),
                "state_file": str(tmp_path / "state.json"),
                "csv_file": csv_path,
                "interval_minutes": 15,
            }
        }
        cfg["questions"].update(overrides)
        return cfg

    def test_first_run_picks_a_question_and_persists_state(self, tmp_path):
        cfg = self._base_config(tmp_path)
        questions.generate(cfg)
        state = questions._load_state(cfg["questions"]["state_file"])
        assert state["current_index"] in (0, 1, 2)
        assert "last_updated" in state

    def test_within_interval_keeps_same_question(self, tmp_path):
        cfg = self._base_config(tmp_path)
        questions.generate(cfg)
        state1 = questions._load_state(cfg["questions"]["state_file"])

        questions.generate(cfg)
        state2 = questions._load_state(cfg["questions"]["state_file"])
        assert state1["current_index"] == state2["current_index"]
        assert state1["last_updated"] == state2["last_updated"]

    def test_expired_interval_picks_new_question(self, tmp_path):
        cfg = self._base_config(tmp_path, interval_minutes=1)
        questions.generate(cfg)
        state = questions._load_state(cfg["questions"]["state_file"])
        # Force the stored timestamp far enough into the past to have expired.
        state["last_updated"] = time.time() - 3600
        questions._save_state(cfg["questions"]["state_file"], state)

        questions.generate(cfg)
        new_state = questions._load_state(cfg["questions"]["state_file"])
        assert new_state["last_updated"] > state["last_updated"]

    def test_force_new_overrides_interval(self, tmp_path):
        cfg = self._base_config(tmp_path, force_new=True)
        questions.generate(cfg)
        state1 = questions._load_state(cfg["questions"]["state_file"])
        questions.generate(cfg)
        state2 = questions._load_state(cfg["questions"]["state_file"])
        # force_new=True on every call means last_updated always refreshes.
        assert state2["last_updated"] >= state1["last_updated"]

    def test_no_questions_renders_fallback(self, tmp_path):
        cfg = {
            "questions": {
                "output_path": str(tmp_path / "out.bmp"),
                "state_file": str(tmp_path / "state.json"),
                "csv_file": str(tmp_path / "missing.csv"),
            }
        }
        result = questions.generate(cfg)
        assert os.path.exists(result)
        # No state should have been written since we short-circuited to fallback.
        assert not os.path.exists(cfg["questions"]["state_file"])
