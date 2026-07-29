"""
Unit tests for modules/wiki_image.py

Covers featured-content parsing (image URL resolution, caption HTML
stripping, thumbnail fallback), download failure handling, and the
generate() fallback to an error image, all mocked — no real network calls.
"""

import sys
import os
from datetime import date
from unittest.mock import patch, MagicMock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.wiki_image import _fetch_featured, _download_image, generate


class TestFetchFeatured:
    @patch("modules.wiki_image.requests.get")
    def test_prefers_full_source_image_url(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "image": {
                "image": {"source": "https://example.com/full.jpg"},
                "thumbnail": {"source": "https://example.com/thumb.jpg"},
                "description": {"text": "A caption"},
            }
        }
        mock_get.return_value = resp

        url, caption = _fetch_featured(date(2024, 1, 1))
        assert url == "https://example.com/full.jpg"
        assert caption == "A caption"

    @patch("modules.wiki_image.requests.get")
    def test_falls_back_to_thumbnail_when_no_full_source(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "image": {
                "image": {},
                "thumbnail": {"source": "https://example.com/thumb.jpg"},
                "title": "Some Title",
            }
        }
        mock_get.return_value = resp

        url, caption = _fetch_featured(date(2024, 1, 1))
        assert url == "https://example.com/thumb.jpg"
        assert caption == "Some Title"

    @patch("modules.wiki_image.requests.get")
    def test_strips_html_tags_from_caption(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "image": {
                "image": {"source": "https://example.com/full.jpg"},
                "description": {"text": "A <b>bold</b> caption with <i>italics</i>"},
            }
        }
        mock_get.return_value = resp

        _url, caption = _fetch_featured(date(2024, 1, 1))
        assert caption == "A bold caption with italics"

    @patch("modules.wiki_image.requests.get")
    def test_no_image_data_falls_back_to_default_caption(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"image": {}}
        mock_get.return_value = resp

        url, caption = _fetch_featured(date(2024, 1, 1))
        assert url is None
        assert caption == "Wikipedia Picture of the Day"

    @patch("modules.wiki_image.requests.get")
    def test_request_exception_returns_none_none(self, mock_get):
        mock_get.side_effect = Exception("network down")
        url, caption = _fetch_featured(date(2024, 1, 1))
        assert url is None
        assert caption is None

    @patch("modules.wiki_image.requests.get")
    def test_url_built_from_given_date(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"image": {}}
        mock_get.return_value = resp

        _fetch_featured(date(2023, 3, 5))
        called_url = mock_get.call_args[0][0]
        assert "2023/03/05" in called_url


class TestDownloadImage:
    @patch("modules.wiki_image.requests.get")
    def test_download_failure_returns_none(self, mock_get):
        mock_get.side_effect = Exception("connection reset")
        assert _download_image("https://example.com/img.jpg") is None


class TestGenerate:
    @patch("modules.wiki_image._fetch_featured")
    def test_no_image_url_produces_error_image(self, mock_fetch, tmp_path):
        mock_fetch.return_value = (None, None)
        output_path = str(tmp_path / "out.bmp")
        config = {"wiki_image": {"output_path": output_path}, "width": 200, "height": 100}

        result = generate(config)
        assert result == output_path
        assert os.path.exists(output_path)

    @patch("modules.wiki_image._download_image")
    @patch("modules.wiki_image._fetch_featured")
    def test_download_failure_produces_error_image(self, mock_fetch, mock_download, tmp_path):
        mock_fetch.return_value = ("https://example.com/img.jpg", "caption")
        mock_download.return_value = None
        output_path = str(tmp_path / "out.bmp")
        config = {"wiki_image": {"output_path": output_path}, "width": 200, "height": 100}

        result = generate(config)
        assert result == output_path
        assert os.path.exists(output_path)

    def test_uses_daily_cache_when_output_path_matches(self, tmp_path, monkeypatch):
        import modules.wiki_image as wiki_image

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(wiki_image, "CACHE_DIR", str(tmp_path / "data"))
        os.makedirs(str(tmp_path / "data"), exist_ok=True)
        cached_path = wiki_image._today_cache_path()
        # Write a dummy cached file
        with open(cached_path, "w") as f:
            f.write("dummy")

        with patch.object(wiki_image, "_fetch_featured") as mock_fetch:
            config = {"wiki_image": {"output_path": cached_path}, "width": 800, "height": 480}
            result = wiki_image.generate(config)
            mock_fetch.assert_not_called()
        assert result == cached_path
