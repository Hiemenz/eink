"""
Unit tests for modules/on_this_day.py.
"""

import json
import os
import sys
from unittest.mock import patch, MagicMock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules import on_this_day as otd


class TestSelectEvents:
    def test_empty_events_returns_empty(self):
        assert otd._select_events([]) == []
        assert otd._select_events(None) == []

    def test_prefers_short_events(self):
        events = [
            {"year": 2000, "text": "x" * 200},  # too long, excluded from "short" pool
            {"year": 1990, "text": "short event"},
        ]
        result = otd._select_events(events)
        assert all(len(e["text"]) < 120 for e in result)

    def test_falls_back_to_all_events_if_none_short(self):
        events = [{"year": 2000, "text": "x" * 200}]
        result = otd._select_events(events)
        assert len(result) == 1

    def test_sorted_by_year_ascending(self):
        events = [
            {"year": 2005, "text": "c"},
            {"year": 1900, "text": "a"},
            {"year": 1950, "text": "b"},
        ]
        result = otd._select_events(events)
        years = [e["year"] for e in result]
        assert years == sorted(years)

    def test_respects_max_count(self):
        events = [{"year": y, "text": f"event {y}"} for y in range(20)]
        result = otd._select_events(events, max_count=3)
        assert len(result) == 3

    def test_missing_year_defaults_to_zero(self):
        events = [{"text": "no year here"}]
        result = otd._select_events(events)
        assert result[0].get("year", 0) == 0


class TestFetchEvents:
    @patch("modules.on_this_day.requests.get")
    def test_success_returns_events(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"events": [{"year": 2000, "text": "Something happened"}]}
        mock_get.return_value = resp
        events = otd._fetch_events()
        assert events == [{"year": 2000, "text": "Something happened"}]

    @patch("modules.on_this_day.requests.get")
    def test_missing_events_key_returns_empty_list(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {}
        mock_get.return_value = resp
        assert otd._fetch_events() == []

    @patch("modules.on_this_day.requests.get")
    def test_network_failure_returns_none(self, mock_get):
        mock_get.side_effect = ConnectionError("boom")
        assert otd._fetch_events() is None

    @patch("modules.on_this_day.requests.get")
    def test_http_error_returns_none(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception("404")
        mock_get.return_value = resp
        assert otd._fetch_events() is None

    @patch("modules.on_this_day.requests.get")
    def test_malformed_json_returns_none(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.side_effect = ValueError("bad json")
        mock_get.return_value = resp
        assert otd._fetch_events() is None


class TestCache:
    def test_load_cache_missing_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(otd, "CACHE_DIR", str(tmp_path))
        assert otd._load_cache() is None

    def test_save_then_load_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(otd, "CACHE_DIR", str(tmp_path))
        data = [{"year": 2000, "text": "event"}]
        otd._save_cache(data)
        assert otd._load_cache() == data

    def test_cache_path_includes_todays_date(self, tmp_path, monkeypatch):
        from datetime import date
        monkeypatch.setattr(otd, "CACHE_DIR", str(tmp_path))
        path = otd._cache_path()
        assert date.today().isoformat() in path


class TestWrapText:
    def test_wraps_long_text(self):
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (10, 10))
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default()
        lines = otd._wrap_text(draw, "one two three four five six seven eight", font, 40)
        assert len(lines) > 1

    def test_empty_text_returns_empty_list(self):
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (10, 10))
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default()
        assert otd._wrap_text(draw, "", font, 1000) == []

    def test_single_word_fits_on_one_line(self):
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (10, 10))
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default()
        assert otd._wrap_text(draw, "hello", font, 1000) == ["hello"]


class TestCacheCorruption:
    def test_corrupt_cache_file_returns_none(self, tmp_path, monkeypatch):
        """A corrupt cache file must degrade gracefully like _fetch_events
        does, not crash the whole module with a JSONDecodeError."""
        monkeypatch.setattr(otd, "CACHE_DIR", str(tmp_path))
        path = otd._cache_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("{not valid json")
        assert otd._load_cache() is None


class TestRender:
    def test_render_with_events_creates_file(self, tmp_path):
        output_path = str(tmp_path / "out.bmp")
        events = [{"year": 2000, "text": "Something happened"}]
        result = otd._render(events, output_path)
        assert result == output_path
        assert os.path.exists(output_path)

    def test_render_output_has_requested_dimensions(self, tmp_path):
        from PIL import Image
        output_path = str(tmp_path / "out.bmp")
        otd._render([{"year": 1999, "text": "Event"}], output_path, width=400, height=300)
        img = Image.open(output_path)
        assert img.size == (400, 300)

    def test_render_no_events_still_creates_file(self, tmp_path):
        output_path = str(tmp_path / "out.bmp")
        result = otd._render([], output_path)
        assert result == output_path
        assert os.path.exists(output_path)

    def test_render_long_event_text_does_not_crash(self, tmp_path):
        output_path = str(tmp_path / "out.bmp")
        events = [{"year": 1, "text": "x" * 500}]
        result = otd._render(events, output_path)
        assert os.path.exists(result)


class TestGenerate:
    def test_uses_fresh_cache_without_fetching(self, tmp_path, monkeypatch):
        monkeypatch.setattr(otd, "CACHE_DIR", str(tmp_path))
        otd._save_cache([{"year": 2000, "text": "Cached event"}])
        output_path = str(tmp_path / "out.bmp")
        with patch("modules.on_this_day.requests.get") as mock_get:
            result = otd.generate({"on_this_day": {"output_path": output_path}})
        mock_get.assert_not_called()
        assert os.path.exists(result)

    @patch("modules.on_this_day.requests.get")
    def test_no_cache_fetches_and_saves(self, mock_get, tmp_path, monkeypatch):
        monkeypatch.setattr(otd, "CACHE_DIR", str(tmp_path))
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"events": [{"year": 2010, "text": "Fetched event"}]}
        mock_get.return_value = resp
        output_path = str(tmp_path / "out.bmp")

        result = otd.generate({"on_this_day": {"output_path": output_path}})

        assert os.path.exists(result)
        assert otd._load_cache() == [{"year": 2010, "text": "Fetched event"}]

    @patch("modules.on_this_day.requests.get")
    def test_fetch_failure_renders_fallback(self, mock_get, tmp_path, monkeypatch):
        monkeypatch.setattr(otd, "CACHE_DIR", str(tmp_path))
        mock_get.side_effect = ConnectionError("boom")
        output_path = str(tmp_path / "out.bmp")

        result = otd.generate({"on_this_day": {"output_path": output_path}})

        assert os.path.exists(result)

    def test_default_output_path_used_when_unconfigured(self, tmp_path, monkeypatch):
        monkeypatch.setattr(otd, "CACHE_DIR", str(tmp_path))
        otd._save_cache([{"year": 2000, "text": "Cached event"}])
        monkeypatch.chdir(tmp_path)
        result = otd.generate({})
        assert result == "onthisday_display.bmp"
        assert os.path.exists(result)
