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

from modules import xkcd as xkcd_mod
from modules.xkcd import (
    _choose_comic,
    _wrap,
    _wrap_clamped,
    _cache_paths,
    _save_cache,
    _load_cache,
    _fit_font,
    _error_image,
    _render,
    generate,
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


class TestFitFont:
    def _draw(self):
        return ImageDraw.Draw(Image.new("RGB", (800, 480)))

    def test_short_text_uses_start_size(self):
        draw = self._draw()
        font = _fit_font(draw, "Hi", 700, start_size=26, min_size=14, bold=True)
        # A 2-char string at 700px width should fit at the largest size tried.
        assert font.size == 26

    def test_long_text_shrinks_below_start_size(self):
        draw = self._draw()
        text = "A very long xkcd title that will not fit in a narrow column"
        font = _fit_font(draw, text, 120, start_size=26, min_size=14, bold=True)
        assert font.size < 26

    def test_never_returns_below_min_size(self):
        draw = self._draw()
        text = "x" * 500
        font = _fit_font(draw, text, 10, start_size=26, min_size=14, bold=True)
        assert font.size == 14


class TestErrorImage:
    def test_creates_file_with_default_message(self, tmp_path):
        output_path = str(tmp_path / "err.bmp")
        result = _error_image(output_path)
        assert result == output_path
        assert os.path.exists(output_path)
        img = Image.open(output_path)
        assert img.size == (xkcd_mod.WIDTH, xkcd_mod.HEIGHT)

    def test_creates_parent_dirs(self, tmp_path):
        output_path = str(tmp_path / "nested" / "dir" / "err.bmp")
        _error_image(output_path, message="Custom message")
        assert os.path.exists(output_path)


class TestRender:
    def _comic_img(self, size=(200, 150)):
        return Image.new("RGB", size, "blue")

    def test_render_basic_comic(self, tmp_path):
        output_path = str(tmp_path / "comic.bmp")
        meta = {"num": 42, "title": "Test Comic", "alt": "A funny hover joke",
                 "year": "2024", "month": "3", "day": "15"}
        result = _render(meta, self._comic_img(), output_path)
        assert result == output_path
        img = Image.open(output_path)
        assert img.size == (xkcd_mod.WIDTH, xkcd_mod.HEIGHT)

    def test_render_missing_date_fields_omits_date(self, tmp_path):
        """Malformed/missing year-month-day must not raise -- date_str just becomes empty."""
        output_path = str(tmp_path / "comic.bmp")
        meta = {"num": 1, "title": "No Date", "alt": ""}
        result = _render(meta, self._comic_img(), output_path)
        assert os.path.exists(result)

    def test_render_empty_alt_text_no_caption_lines(self, tmp_path):
        output_path = str(tmp_path / "comic.bmp")
        meta = {"num": 1, "title": "Silent", "alt": "", "year": "2024", "month": "1", "day": "1"}
        result = _render(meta, self._comic_img(), output_path)
        assert os.path.exists(result)

    def test_render_long_alt_text_gets_clamped(self, tmp_path):
        output_path = str(tmp_path / "comic.bmp")
        meta = {
            "num": 1, "title": "Wordy",
            "alt": " ".join(["hover"] * 100),
            "year": "2024", "month": "1", "day": "1",
        }
        result = _render(meta, self._comic_img(), output_path)
        assert os.path.exists(result)

    def test_render_wide_comic_image_is_letterboxed(self, tmp_path):
        output_path = str(tmp_path / "comic.bmp")
        meta = {"num": 1, "title": "Wide", "alt": "", "year": "2024", "month": "1", "day": "1"}
        result = _render(meta, self._comic_img(size=(2000, 100)), output_path)
        assert os.path.exists(result)

    def test_render_missing_num_key(self, tmp_path):
        output_path = str(tmp_path / "comic.bmp")
        meta = {"title": "No number field", "alt": ""}
        result = _render(meta, self._comic_img(), output_path)
        assert os.path.exists(result)


class TestGenerate:
    def _meta(self):
        return {"num": 99, "title": "Fetched", "alt": "caption",
                "year": "2024", "month": "5", "day": "1", "img": "https://xkcd.com/99.png"}

    @patch("modules.xkcd._download_image")
    @patch("modules.xkcd._choose_comic")
    def test_success_path_renders_and_caches(self, mock_choose, mock_download, tmp_path):
        mock_choose.return_value = self._meta()
        mock_download.return_value = Image.new("RGB", (100, 100), "green")
        output_path = str(tmp_path / "out.bmp")
        cache_dir = str(tmp_path / "cache")

        result = generate({"xkcd": {"output_path": output_path, "cache_dir": cache_dir, "mode": "latest"}})

        assert result == output_path
        assert os.path.exists(output_path)
        meta_path, img_path = _cache_paths(cache_dir)
        assert os.path.exists(meta_path)
        assert os.path.exists(img_path)

    @patch("modules.xkcd._download_image")
    @patch("modules.xkcd._choose_comic")
    def test_fetch_failure_falls_back_to_cache(self, mock_choose, mock_download, tmp_path):
        cache_dir = str(tmp_path / "cache")
        _save_cache(cache_dir, self._meta(), Image.new("RGB", (50, 50), "red"))

        mock_choose.side_effect = Exception("network error")
        output_path = str(tmp_path / "out.bmp")

        result = generate({"xkcd": {"output_path": output_path, "cache_dir": cache_dir}})

        assert result == output_path
        assert os.path.exists(output_path)
        mock_download.assert_not_called()

    @patch("modules.xkcd._download_image")
    @patch("modules.xkcd._choose_comic")
    def test_fetch_failure_no_cache_renders_error_image(self, mock_choose, mock_download, tmp_path):
        mock_choose.side_effect = Exception("network error")
        output_path = str(tmp_path / "out.bmp")
        cache_dir = str(tmp_path / "empty_cache")

        result = generate({"xkcd": {"output_path": output_path, "cache_dir": cache_dir}})

        assert result == output_path
        assert os.path.exists(output_path)

    @patch("modules.xkcd._download_image")
    @patch("modules.xkcd._choose_comic")
    def test_mode_is_lowercased_and_stripped(self, mock_choose, mock_download, tmp_path):
        mock_choose.return_value = self._meta()
        mock_download.return_value = Image.new("RGB", (100, 100), "green")
        output_path = str(tmp_path / "out.bmp")

        generate({"xkcd": {"output_path": output_path, "cache_dir": str(tmp_path / "c"), "mode": "  DAILY  "}})

        mock_choose.assert_called_once_with("daily")

    @patch("modules.xkcd._download_image")
    @patch("modules.xkcd._choose_comic")
    def test_default_output_path_and_mode(self, mock_choose, mock_download, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_choose.return_value = self._meta()
        mock_download.return_value = Image.new("RGB", (100, 100), "green")

        result = generate({})

        assert result == "images/xkcd_display.bmp"
        assert os.path.exists(result)
        mock_choose.assert_called_once_with("latest")
