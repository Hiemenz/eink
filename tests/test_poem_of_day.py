"""
Unit tests for modules/poem_of_day.py.
"""

import os
import sys
from unittest.mock import patch, MagicMock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules import poem_of_day as pod


class TestFetchPoem:
    @patch("modules.poem_of_day.requests.get")
    def test_success_returns_poem_dict(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = [{
            "title": "Hope",
            "author": "Emily Dickinson",
            "lines": ["line one", "line two"],
        }]
        mock_get.return_value = resp
        poem = pod._fetch_poem()
        assert poem == {"title": "Hope", "author": "Emily Dickinson", "lines": ["line one", "line two"]}

    @patch("modules.poem_of_day.requests.get")
    def test_missing_title_returns_none(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = [{"title": "", "author": "X", "lines": ["a"]}]
        mock_get.return_value = resp
        assert pod._fetch_poem() is None

    @patch("modules.poem_of_day.requests.get")
    def test_missing_lines_returns_none(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = [{"title": "T", "author": "A", "lines": []}]
        mock_get.return_value = resp
        assert pod._fetch_poem() is None

    @patch("modules.poem_of_day.requests.get")
    def test_non_list_payload_returns_none(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"error": "not found"}
        mock_get.return_value = resp
        assert pod._fetch_poem() is None

    @patch("modules.poem_of_day.requests.get")
    def test_empty_list_payload_returns_none(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = []
        mock_get.return_value = resp
        assert pod._fetch_poem() is None

    @patch("modules.poem_of_day.requests.get")
    def test_network_failure_returns_none(self, mock_get):
        mock_get.side_effect = ConnectionError("boom")
        assert pod._fetch_poem() is None

    @patch("modules.poem_of_day.requests.get")
    def test_http_error_returns_none(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception("500")
        mock_get.return_value = resp
        assert pod._fetch_poem() is None


class TestCache:
    def test_load_cache_missing_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pod, "CACHE_DIR", str(tmp_path))
        assert pod._load_cache() is None

    def test_save_then_load_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pod, "CACHE_DIR", str(tmp_path))
        data = {"title": "T", "author": "A", "lines": ["l1"]}
        pod._save_cache(data)
        assert pod._load_cache() == data


class TestFitLinesFont:
    def _draw(self):
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (10, 10))
        return ImageDraw.Draw(img)

    def test_returns_largest_size_that_fits(self):
        draw = self._draw()
        lines = ["short line"]
        font, size = pod._fit_lines_font(
            draw, lines, pod._font_path(), max_width=2000, max_height=2000,
            start_size=22, min_size=10,
        )
        assert size == 22  # plenty of room, should pick the largest

    def test_shrinks_when_space_is_tight(self):
        draw = self._draw()
        lines = ["a very long line of poetry that needs to be squeezed in"]
        font, size = pod._fit_lines_font(
            draw, lines, pod._font_path(), max_width=100, max_height=20,
            start_size=22, min_size=10,
        )
        assert size == 10  # falls back to min_size when nothing fits


class TestTruncateLines:
    def _draw(self):
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (10, 10))
        return ImageDraw.Draw(img)

    def test_no_truncation_when_lines_fit(self):
        draw = self._draw()
        font = pod.ImageFont.load_default()
        lines = ["line1", "line2"]
        result = pod._truncate_lines(draw, lines, font, max_width=1000, max_height=1000)
        assert result == lines

    def test_truncates_and_adds_ellipsis(self):
        draw = self._draw()
        font = pod.ImageFont.load_default()
        lines = [f"line{i}" for i in range(50)]
        line_h = draw.textbbox((0, 0), "Ay", font=font)[3] + 4
        max_height = line_h * 3  # room for only 3 lines
        result = pod._truncate_lines(draw, lines, font, max_width=1000, max_height=max_height)
        assert result[-1] == "..."
        assert len(result) == 3


class TestCachePath:
    def test_cache_path_includes_todays_date(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pod, "CACHE_DIR", str(tmp_path))
        from datetime import date
        today = date.today().isoformat()
        assert today in pod._cache_path()

    def test_cache_path_under_cache_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pod, "CACHE_DIR", str(tmp_path))
        assert pod._cache_path().startswith(str(tmp_path))


class TestRender:
    def test_render_creates_output_file_at_default_size(self, tmp_path):
        from PIL import Image
        out = str(tmp_path / "poem.bmp")
        result = pod._render("A Title", "An Author", ["line one", "line two"], out)
        assert result == out
        img = Image.open(out)
        assert img.size == (800, 480)

    def test_render_handles_all_blank_lines(self, tmp_path):
        out = str(tmp_path / "poem.bmp")
        pod._render("Title", "Author", ["", "  ", ""], out)  # must not raise
        assert os.path.exists(out)

    def test_render_strips_leading_and_trailing_blank_lines(self, tmp_path):
        out = str(tmp_path / "poem.bmp")
        # Should behave the same as without the blank padding — just verifying
        # this doesn't crash and still produces a normal-sized image.
        pod._render("Title", "Author", ["", "real line", ""], out)
        from PIL import Image
        assert Image.open(out).size == (800, 480)

    def test_render_handles_very_long_title(self, tmp_path):
        """Title wider than the canvas at every size down to 16 must still render
        (exercises the for/else fallback when no title_size ever fits)."""
        out = str(tmp_path / "poem.bmp")
        long_title = "A Very Long Title That Will Never Fit On One Line No Matter The Font Size Chosen"
        pod._render(long_title, "Author", ["line"], out)  # must not raise
        assert os.path.exists(out)

    def test_render_handles_many_lines_triggering_truncation(self, tmp_path):
        out = str(tmp_path / "poem.bmp")
        lines = [f"line {i}" for i in range(60)]
        pod._render("Title", "Author", lines, out)
        assert os.path.exists(out)


class TestGenerateFallback:
    def test_generate_uses_hardcoded_fallback_on_total_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pod, "CACHE_DIR", str(tmp_path))
        monkeypatch.chdir(tmp_path)
        with patch("modules.poem_of_day._fetch_poem", return_value=None):
            output_path = pod.generate({"poem_of_day": {"output_path": str(tmp_path / "out.bmp")}})
        assert os.path.exists(output_path)

    def test_generate_uses_fresh_cache_without_fetching(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pod, "CACHE_DIR", str(tmp_path))
        monkeypatch.chdir(tmp_path)
        cached = {"title": "Cached", "author": "Cacher", "lines": ["l1"]}
        pod._save_cache(cached)
        with patch("modules.poem_of_day._fetch_poem") as mock_fetch:
            output_path = pod.generate({"poem_of_day": {"output_path": str(tmp_path / "out.bmp")}})
        mock_fetch.assert_not_called()
        assert os.path.exists(output_path)

    def test_generate_saves_cache_on_successful_fetch(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pod, "CACHE_DIR", str(tmp_path))
        monkeypatch.chdir(tmp_path)
        fetched = {"title": "Fresh", "author": "Fetcher", "lines": ["l1"]}
        with patch("modules.poem_of_day._fetch_poem", return_value=fetched):
            pod.generate({"poem_of_day": {"output_path": str(tmp_path / "out.bmp")}})
        assert pod._load_cache() == fetched

    def test_generate_default_output_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pod, "CACHE_DIR", str(tmp_path))
        monkeypatch.chdir(tmp_path)
        with patch("modules.poem_of_day._fetch_poem", return_value=None):
            output_path = pod.generate({})
        assert output_path == "poem_display.bmp"
        assert os.path.exists(output_path)
