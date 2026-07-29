"""
Unit tests for modules/saint_of_day.py.
"""

import os
import sys
from unittest.mock import patch, MagicMock
from io import BytesIO

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules import saint_of_day as sod


SAMPLE_HTML = """
<html><body>
<article>
<h1>St. Example the Confessor</h1>
<img src="https://example.com/portrait.jpg" />
<p>Short.</p>
<p>Feast day: January 15</p>
<p>This is a much longer biography paragraph about the saint's life and works, definitely over eighty characters long.</p>
</article>
</body></html>
"""

SAMPLE_HTML_NO_IMAGE = """
<html><body>
<article>
<h2>St. Nobody</h2>
<p>This is a much longer biography paragraph about the saint's life, definitely over eighty characters long here.</p>
</article>
</body></html>
"""


class TestWrapText:
    def _draw(self):
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (10, 10))
        return ImageDraw.Draw(img)

    def test_wraps_long_text(self):
        from PIL import ImageFont
        draw = self._draw()
        font = ImageFont.load_default()
        lines = sod._wrap_text(draw, "one two three four five six seven eight", font, 40)
        assert len(lines) > 1

    def test_short_text_one_line(self):
        from PIL import ImageFont
        draw = self._draw()
        font = ImageFont.load_default()
        assert sod._wrap_text(draw, "hi", font, 1000) == ["hi"]


class TestFitFont:
    def _draw(self):
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (10, 10))
        return ImageDraw.Draw(img)

    def test_shrinks_to_fit_small_box(self):
        draw = self._draw()
        long_text = "A very long biography that will not fit in a tiny box no matter the font size chosen here"
        font, lines = sod._fit_font(draw, long_text, sod._font_path(), max_width=80, max_height=20, start_size=48, min_size=12)
        assert font is not None
        assert isinstance(lines, list)

    def test_picks_large_size_with_room(self):
        draw = self._draw()
        font, lines = sod._fit_font(draw, "short bio", sod._font_path(), max_width=2000, max_height=2000, start_size=48, min_size=12)
        assert lines == ["short bio"]


class TestCache:
    def test_load_cache_missing_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sod, "CACHE_DIR", str(tmp_path))
        assert sod._load_cache() is None

    def test_save_then_load_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sod, "CACHE_DIR", str(tmp_path))
        data = {"name": "St. Test", "feast_day": "Jan 1", "bio": "bio text", "image_url": None}
        sod._save_cache(data)
        assert sod._load_cache() == data


class TestScrapeFranciscan:
    @patch("modules.saint_of_day.requests.get")
    def test_parses_name_bio_feast_and_image(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.text = SAMPLE_HTML
        mock_get.return_value = resp

        data = sod._scrape_franciscan()
        assert data["name"] == "St. Example the Confessor"
        assert data["image_url"] == "https://example.com/portrait.jpg"
        assert "biography" in data["bio"]
        assert "January 15" in data["feast_day"] or "Feast day" in data["feast_day"]

    @patch("modules.saint_of_day.requests.get")
    def test_missing_image_returns_none_url(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.text = SAMPLE_HTML_NO_IMAGE
        mock_get.return_value = resp

        data = sod._scrape_franciscan()
        assert data["image_url"] is None
        assert data["name"] == "St. Nobody"

    @patch("modules.saint_of_day.requests.get")
    def test_no_heading_falls_back_to_default_name(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.text = "<html><body><article><p>Some text with no headings at all here really long enough.</p></article></body></html>"
        mock_get.return_value = resp

        data = sod._scrape_franciscan()
        assert data["name"] == "Saint of the Day"

    @patch("modules.saint_of_day.requests.get")
    def test_network_failure_returns_none(self, mock_get):
        mock_get.side_effect = ConnectionError("boom")
        assert sod._scrape_franciscan() is None

    @patch("modules.saint_of_day.requests.get")
    def test_http_error_returns_none(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception("500")
        mock_get.return_value = resp
        assert sod._scrape_franciscan() is None

    @patch("modules.saint_of_day.requests.get")
    def test_relative_image_src_is_ignored(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.text = """
        <html><body><article>
        <h1>St. Relative</h1>
        <img src="/images/relative.jpg" />
        <p>A sufficiently long biography paragraph to be picked up by the scraper here.</p>
        </article></body></html>
        """
        mock_get.return_value = resp
        data = sod._scrape_franciscan()
        assert data["image_url"] is None


class TestFetchPortrait:
    def test_none_url_returns_none(self):
        assert sod._fetch_portrait(None) is None

    def test_empty_url_returns_none(self):
        assert sod._fetch_portrait("") is None

    @patch("modules.saint_of_day.requests.get")
    def test_network_failure_returns_none(self, mock_get):
        mock_get.side_effect = ConnectionError("boom")
        assert sod._fetch_portrait("https://example.com/x.jpg") is None

    @patch("modules.saint_of_day.requests.get")
    def test_success_returns_pil_image(self, mock_get):
        from PIL import Image
        buf = BytesIO()
        Image.new("RGB", (10, 10), "red").save(buf, format="PNG")

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.content = buf.getvalue()
        mock_get.return_value = resp

        img = sod._fetch_portrait("https://example.com/x.png")
        assert img is not None
        assert img.mode == "RGB"

    @patch("modules.saint_of_day.requests.get")
    def test_corrupt_image_bytes_returns_none(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.content = b"not an image"
        mock_get.return_value = resp
        assert sod._fetch_portrait("https://example.com/x.png") is None


class TestGenerateFallback:
    def test_generate_falls_back_when_scrape_fails(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sod, "CACHE_DIR", str(tmp_path))
        with patch("modules.saint_of_day._scrape_franciscan", return_value=None), \
             patch("modules.saint_of_day._fetch_portrait", return_value=None):
            output_path = sod.generate({"saint_of_day": {"output_path": str(tmp_path / "out.bmp")}})
        assert os.path.exists(output_path)
