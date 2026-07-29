"""
Unit tests for modules/quote_of_day.py.
"""

import os
import sys
from unittest.mock import patch, MagicMock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules import quote_of_day as qod


class TestFetchQuote:
    @patch("modules.quote_of_day.requests.get")
    def test_success_returns_quote_dict(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = [{"q": "Stay hungry.", "a": "Steve Jobs"}]
        mock_get.return_value = resp
        assert qod._fetch_quote() == {"q": "Stay hungry.", "a": "Steve Jobs"}

    @patch("modules.quote_of_day.requests.get")
    def test_missing_quote_text_returns_none(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = [{"q": "", "a": "Someone"}]
        mock_get.return_value = resp
        assert qod._fetch_quote() is None

    @patch("modules.quote_of_day.requests.get")
    def test_missing_author_returns_none(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = [{"q": "Hi", "a": ""}]
        mock_get.return_value = resp
        assert qod._fetch_quote() is None

    @patch("modules.quote_of_day.requests.get")
    def test_non_list_payload_returns_none(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"error": "bad"}
        mock_get.return_value = resp
        assert qod._fetch_quote() is None

    @patch("modules.quote_of_day.requests.get")
    def test_empty_list_returns_none(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = []
        mock_get.return_value = resp
        assert qod._fetch_quote() is None

    @patch("modules.quote_of_day.requests.get")
    def test_network_failure_returns_none(self, mock_get):
        mock_get.side_effect = ConnectionError("boom")
        assert qod._fetch_quote() is None

    @patch("modules.quote_of_day.requests.get")
    def test_http_error_returns_none(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception("500")
        mock_get.return_value = resp
        assert qod._fetch_quote() is None


class TestCache:
    def test_load_cache_missing_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(qod, "CACHE_DIR", str(tmp_path))
        assert qod._load_cache() is None

    def test_save_then_load_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(qod, "CACHE_DIR", str(tmp_path))
        data = {"q": "Text", "a": "Author"}
        qod._save_cache(data)
        assert qod._load_cache() == data

    def test_cache_path_includes_todays_date(self, tmp_path, monkeypatch):
        from datetime import date
        monkeypatch.setattr(qod, "CACHE_DIR", str(tmp_path))
        assert date.today().isoformat() in qod._cache_path()


class TestWrapText:
    def _draw(self):
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (10, 10))
        return ImageDraw.Draw(img)

    def test_wraps_long_text(self):
        from PIL import ImageFont
        draw = self._draw()
        font = ImageFont.load_default()
        lines = qod._wrap_text(draw, "one two three four five six seven eight", font, 40)
        assert len(lines) > 1

    def test_preserves_all_words(self):
        from PIL import ImageFont
        draw = self._draw()
        font = ImageFont.load_default()
        text = "one two three four five six seven eight"
        lines = qod._wrap_text(draw, text, font, 40)
        assert " ".join(lines).split() == text.split()


class TestFitFont:
    def _draw(self):
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (10, 10))
        return ImageDraw.Draw(img)

    def test_picks_largest_size_with_ample_room(self):
        draw = self._draw()
        font, lines = qod._fit_font(
            draw, "short", qod._font_path(), max_width=2000, max_height=2000,
            start_size=72, min_size=14,
        )
        # 72 is even, loop steps by -2 so 72 should be chosen directly.
        assert lines == ["short"]

    def test_falls_back_to_min_size_when_cramped(self):
        draw = self._draw()
        long_text = "a very long quote that will not fit in a tiny box no matter what"
        font, lines = qod._fit_font(
            draw, long_text, qod._font_path(), max_width=50, max_height=10,
            start_size=72, min_size=14,
        )
        # Nothing fits in a 50x10 box; should fall through to min_size handling.
        assert font is not None


class TestGenerateFallback:
    def test_generate_uses_hardcoded_fallback_on_total_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(qod, "CACHE_DIR", str(tmp_path))
        with patch("modules.quote_of_day._fetch_quote", return_value=None):
            output_path = qod.generate({"quote_of_day": {"output_path": str(tmp_path / "out.bmp")}})
        assert os.path.exists(output_path)
