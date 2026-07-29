"""
Unit tests for modules/xkcd.py

Covers comic-choice logic (latest vs. daily mode, the #404 dodge, daily
fetch failure fallback), text wrapping/clamping helpers, and the on-disk
cache round trip. Network calls (_fetch_latest/_fetch_num) are mocked.
"""

import sys
import os
import json
from unittest.mock import patch, MagicMock

import pytest
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.xkcd import (
    _choose_comic,
    _wrap,
    _wrap_clamped,
    _cache_paths,
    _save_cache,
    _load_cache,
)


class TestChooseComic:
    @patch("modules.xkcd._fetch_latest")
    def test_latest_mode_returns_latest_without_extra_fetch(self, mock_latest):
        mock_latest.return_value = {"num": 100, "title": "Latest"}
        with patch("modules.xkcd._fetch_num") as mock_num:
            result = _choose_comic("latest")
            mock_num.assert_not_called()
        assert result == {"num": 100, "title": "Latest"}

    @patch("modules.xkcd._fetch_num")
    @patch("modules.xkcd._fetch_latest")
    def test_daily_mode_fetches_a_specific_comic(self, mock_latest, mock_num):
        mock_latest.return_value = {"num": 2000, "title": "Latest"}
        mock_num.return_value = {"num": 500, "title": "Old Comic"}

        result = _choose_comic("daily")
        # Either the specific comic or (rarely, if rng picked latest_num) the latest.
        assert result in ({"num": 500, "title": "Old Comic"}, {"num": 2000, "title": "Latest"})

    @patch("modules.xkcd._fetch_num")
    @patch("modules.xkcd._fetch_latest")
    def test_daily_mode_never_chooses_404(self, mock_latest, mock_num):
        """Comic 404 famously 404s; the module should redirect to 403 instead."""
        mock_latest.return_value = {"num": 500, "title": "Latest"}
        mock_num.return_value = {"num": 403, "title": "Not 404"}

        with patch("modules.xkcd.random.Random") as mock_rng_cls:
            mock_rng = MagicMock()
            mock_rng.randint.return_value = 404
            mock_rng_cls.return_value = mock_rng

            _choose_comic("daily")
            # It must have fetched 403, never 404.
            mock_num.assert_called_once_with(403)

    @patch("modules.xkcd._fetch_num")
    @patch("modules.xkcd._fetch_latest")
    def test_daily_fetch_failure_falls_back_to_latest(self, mock_latest, mock_num):
        mock_latest.return_value = {"num": 500, "title": "Latest"}
        mock_num.side_effect = Exception("404 not found")

        with patch("modules.xkcd.random.Random") as mock_rng_cls:
            mock_rng = MagicMock()
            mock_rng.randint.return_value = 250
            mock_rng_cls.return_value = mock_rng

            result = _choose_comic("daily")
        assert result == {"num": 500, "title": "Latest"}

    @patch("modules.xkcd._fetch_latest")
    def test_daily_mode_with_latest_num_of_1_returns_latest(self, mock_latest):
        mock_latest.return_value = {"num": 1, "title": "First comic"}
        with patch("modules.xkcd._fetch_num") as mock_num:
            result = _choose_comic("daily")
            mock_num.assert_not_called()
        assert result == {"num": 1, "title": "First comic"}


class TestWrap:
    def _draw(self):
        img = Image.new("RGB", (800, 480))
        return ImageDraw.Draw(img)

    def test_short_text_single_line(self):
        draw = self._draw()
        font = draw.getfont()
        lines = _wrap(draw, "short caption", font, 700)
        assert lines == ["short caption"]

    def test_long_text_wraps(self):
        draw = self._draw()
        font = draw.getfont()
        text = " ".join(["word"] * 50)
        lines = _wrap(draw, text, font, 100)
        assert len(lines) > 1


class TestWrapClamped:
    def test_fits_within_max_lines_unchanged(self):
        draw = ImageDraw.Draw(Image.new("RGB", (800, 480)))
        font = draw.getfont()
        lines = _wrap_clamped(draw, "short caption", font, 700, max_lines=3)
        assert lines == ["short caption"]

    def test_truncates_with_ellipsis_when_too_long(self):
        draw = ImageDraw.Draw(Image.new("RGB", (800, 480)))
        font = draw.getfont()
        text = " ".join(["word"] * 100)
        lines = _wrap_clamped(draw, text, font, 100, max_lines=2)
        assert len(lines) == 2
        assert lines[-1].endswith("…")


class TestCache:
    def test_load_cache_missing_returns_none_none(self, tmp_path):
        meta, img = _load_cache(str(tmp_path))
        assert meta is None
        assert img is None

    def test_save_and_load_roundtrip(self, tmp_path):
        meta = {"num": 42, "title": "Test comic"}
        comic_img = Image.new("RGB", (10, 10), "red")
        _save_cache(str(tmp_path), meta, comic_img)

        loaded_meta, loaded_img = _load_cache(str(tmp_path))
        assert loaded_meta == meta
        assert loaded_img.size == (10, 10)

    def test_cache_paths_are_within_cache_dir(self, tmp_path):
        meta_path, img_path = _cache_paths(str(tmp_path))
        assert meta_path.startswith(str(tmp_path))
        assert img_path.startswith(str(tmp_path))
