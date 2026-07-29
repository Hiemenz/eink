"""
Unit tests for modules/nasa_apod.py: cache path derivation, the APOD
metadata fetch, and the generate() cache-hit / video-fallback / missing
image-url branches.
"""

import sys
import os
import shutil
from datetime import date
from unittest.mock import patch, MagicMock

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import modules.nasa_apod as nasa_apod
from modules.nasa_apod import _today_cache_path, _fetch_apod


class TestTodayCachePath:
    def test_includes_todays_iso_date(self):
        path = _today_cache_path()
        today = date.today().isoformat()
        assert today in path
        assert path.startswith(nasa_apod.CACHE_DIR)


class TestFetchApod:
    @patch("modules.nasa_apod.requests.get")
    def test_successful_fetch_returns_json(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"title": "Nebula", "media_type": "image", "url": "http://x/y.jpg"}
        mock_get.return_value = mock_resp
        result = _fetch_apod(api_key="TESTKEY", today="2026-07-28")
        assert result["title"] == "Nebula"
        args, kwargs = mock_get.call_args
        assert kwargs["params"]["api_key"] == "TESTKEY"
        assert kwargs["params"]["date"] == "2026-07-28"

    @patch("modules.nasa_apod.requests.get")
    def test_network_failure_returns_none(self, mock_get):
        mock_get.side_effect = Exception("network down")
        assert _fetch_apod(api_key="TESTKEY") is None

    @patch("modules.nasa_apod.requests.get")
    def test_http_error_returns_none(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("500 error")
        mock_get.return_value = mock_resp
        assert _fetch_apod(api_key="TESTKEY") is None

    @patch("modules.nasa_apod.requests.get")
    def test_defaults_today_when_not_given(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"title": "x"}
        mock_get.return_value = mock_resp
        _fetch_apod(api_key="TESTKEY")
        args, kwargs = mock_get.call_args
        assert kwargs["params"]["date"] == date.today().isoformat()


class TestGenerateCacheAndFallback:
    def _config(self, output_path):
        return {"nasa_apod": {"output_path": output_path, "api_key": "TESTKEY"},
                "width": 800, "height": 480}

    def test_uses_existing_cache_without_fetching(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nasa_apod, "CACHE_DIR", str(tmp_path))
        cached_path = nasa_apod._today_cache_path()
        os.makedirs(os.path.dirname(cached_path), exist_ok=True)
        Image.new("RGB", (800, 480), "blue").save(cached_path)

        output_path = str(tmp_path / "out.bmp")
        config = self._config(output_path)

        with patch("modules.nasa_apod.requests.get") as mock_get:
            result = nasa_apod.generate(config)
            mock_get.assert_not_called()
        assert result == output_path
        assert os.path.exists(output_path)

    def test_metadata_fetch_failure_renders_error_image(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nasa_apod, "CACHE_DIR", str(tmp_path))
        output_path = str(tmp_path / "out.bmp")
        config = self._config(output_path)

        with patch("modules.nasa_apod.requests.get") as mock_get:
            mock_get.side_effect = Exception("down")
            result = nasa_apod.generate(config)
        assert result == output_path
        assert os.path.exists(output_path)

    def test_video_media_type_renders_text_fallback(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nasa_apod, "CACHE_DIR", str(tmp_path))
        output_path = str(tmp_path / "out.bmp")
        config = self._config(output_path)

        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "media_type": "video", "title": "Cool Video",
            "explanation": "A video about space." * 20,
        }
        with patch("modules.nasa_apod.requests.get", return_value=mock_resp):
            result = nasa_apod.generate(config)
        assert result == output_path
        assert os.path.exists(output_path)
        img = Image.open(output_path)
        assert img.size == (800, 480)

    def test_missing_image_url_renders_error_image(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nasa_apod, "CACHE_DIR", str(tmp_path))
        output_path = str(tmp_path / "out.bmp")
        config = self._config(output_path)

        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"media_type": "image", "title": "No URL"}
        with patch("modules.nasa_apod.requests.get", return_value=mock_resp):
            result = nasa_apod.generate(config)
        assert result == output_path
        assert os.path.exists(output_path)

    def test_successful_image_download_renders_and_copies(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nasa_apod, "CACHE_DIR", str(tmp_path))
        output_path = str(tmp_path / "out.bmp")
        config = self._config(output_path)

        apod_resp = MagicMock()
        apod_resp.raise_for_status.return_value = None
        apod_resp.json.return_value = {
            "media_type": "image", "title": "A nice nebula",
            "url": "http://example.com/img.jpg",
        }

        import io
        buf = io.BytesIO()
        Image.new("RGB", (1000, 700), "green").save(buf, format="JPEG")
        img_resp = MagicMock()
        img_resp.raise_for_status.return_value = None
        img_resp.content = buf.getvalue()

        with patch("modules.nasa_apod.requests.get", side_effect=[apod_resp, img_resp]):
            result = nasa_apod.generate(config)

        assert result == output_path
        assert os.path.exists(output_path)
        img = Image.open(output_path)
        assert img.size == (800, 480)
