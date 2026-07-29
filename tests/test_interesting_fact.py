"""
Unit tests for modules/interesting_fact.py: CSV loading/caching,
deterministic time-bucket fact selection, and text wrapping.
"""

import sys
import os
import csv
import time

import pytest
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import modules.interesting_fact as interesting_fact
from modules.interesting_fact import _load_facts, _pick_fact, _wrap


def _write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["topic", "question"])
        for topic, question in rows:
            writer.writerow([topic, question])


class TestLoadFacts:
    def setup_method(self):
        # Reset the module-level cache before each test so tests don't
        # leak state into one another.
        interesting_fact._facts_cache = []
        interesting_fact._facts_cache_path = ""

    def test_loads_rows_from_csv(self, tmp_path):
        csv_path = str(tmp_path / "facts.csv")
        _write_csv(csv_path, [("science", "Water boils at 100C"), ("history", "Rome founded 753 BC")])
        facts = _load_facts(csv_path)
        assert facts == [("science", "Water boils at 100C"), ("history", "Rome founded 753 BC")]

    def test_skips_rows_with_empty_question(self, tmp_path):
        csv_path = str(tmp_path / "facts.csv")
        _write_csv(csv_path, [("science", ""), ("history", "Real fact")])
        facts = _load_facts(csv_path)
        assert facts == [("history", "Real fact")]

    def test_missing_file_returns_empty_list(self, tmp_path):
        facts = _load_facts(str(tmp_path / "nope.csv"))
        assert facts == []

    def test_caches_result_for_same_path(self, tmp_path):
        csv_path = str(tmp_path / "facts.csv")
        _write_csv(csv_path, [("a", "fact one")])
        first = _load_facts(csv_path)
        # Modify the file after first load; cached result should not change.
        _write_csv(csv_path, [("a", "fact one"), ("b", "fact two")])
        second = _load_facts(csv_path)
        assert first == second == [("a", "fact one")]

    def test_reloads_when_path_changes(self, tmp_path):
        csv_path_a = str(tmp_path / "a.csv")
        csv_path_b = str(tmp_path / "b.csv")
        _write_csv(csv_path_a, [("a", "fact a")])
        _write_csv(csv_path_b, [("b", "fact b")])
        first = _load_facts(csv_path_a)
        second = _load_facts(csv_path_b)
        assert first == [("a", "fact a")]
        assert second == [("b", "fact b")]


class TestPickFact:
    def test_picks_a_valid_fact_from_list(self):
        facts = [("t1", "fact1"), ("t2", "fact2"), ("t3", "fact3")]
        result = _pick_fact(facts, interval_minutes=60)
        assert result in facts

    def test_same_time_bucket_yields_same_fact(self, monkeypatch):
        facts = [("t1", "fact1"), ("t2", "fact2"), ("t3", "fact3"), ("t4", "fact4")]
        fixed_time = 1_700_000_000.0
        monkeypatch.setattr(time, "time", lambda: fixed_time)
        result_a = _pick_fact(facts, interval_minutes=60)
        result_b = _pick_fact(facts, interval_minutes=60)
        assert result_a == result_b

    def test_different_time_buckets_can_yield_different_facts(self, monkeypatch):
        facts = [(str(i), f"fact{i}") for i in range(50)]
        seen = set()
        for bucket in range(20):
            monkeypatch.setattr(time, "time", lambda b=bucket: 1_700_000_000.0 + b * 3600)
            seen.add(_pick_fact(facts, interval_minutes=60))
        # With 20 different hourly buckets and 50 facts, expect some variety.
        assert len(seen) > 1

    def test_single_fact_list_always_returns_it(self):
        facts = [("only", "the only fact")]
        for _ in range(5):
            assert _pick_fact(facts, interval_minutes=60) == ("only", "the only fact")


class TestWrap:
    def _draw(self):
        img = Image.new("RGB", (800, 480), "white")
        return ImageDraw.Draw(img)

    def test_short_text_single_line(self):
        draw = self._draw()
        font = ImageFont.load_default()
        wrapped = _wrap("hello world", font, draw, max_width=1000)
        assert wrapped == "hello world"

    def test_long_text_wraps_into_multiple_lines(self):
        draw = self._draw()
        font = ImageFont.load_default()
        text = " ".join(["word"] * 40)
        wrapped = _wrap(text, font, draw, max_width=50)
        assert "\n" in wrapped

    def test_empty_text_returns_empty_string(self):
        draw = self._draw()
        font = ImageFont.load_default()
        assert _wrap("", font, draw, max_width=500) == ""
