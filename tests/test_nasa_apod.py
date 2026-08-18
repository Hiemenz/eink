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

    def test_image_download_failure_renders_error_image(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nasa_apod, "CACHE_DIR", str(tmp_path))
        output_path = str(tmp_path / "out.bmp")
        config = self._config(output_path)

        apod_resp = MagicMock()
        apod_resp.raise_for_status.return_value = None
        apod_resp.json.return_value = {
            "media_type": "image", "title": "Broken link", "url": "http://example.com/img.jpg",
        }
        img_resp = MagicMock()
        img_resp.raise_for_status.side_effect = Exception("404")

        with patch("modules.nasa_apod.requests.get", side_effect=[apod_resp, img_resp]):
            result = nasa_apod.generate(config)
        assert result == output_path
        assert os.path.exists(output_path)

    def test_hdurl_preferred_over_url(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nasa_apod, "CACHE_DIR", str(tmp_path))
        output_path = str(tmp_path / "out.bmp")
        config = self._config(output_path)

        apod_resp = MagicMock()
        apod_resp.raise_for_status.return_value = None
        apod_resp.json.return_value = {
            "media_type": "image", "title": "HD nebula",
            "url": "http://example.com/sd.jpg", "hdurl": "http://example.com/hd.jpg",
        }
        import io
        buf = io.BytesIO()
        Image.new("RGB", (600, 400), "red").save(buf, format="JPEG")
        img_resp = MagicMock()
        img_resp.raise_for_status.return_value = None
        img_resp.content = buf.getvalue()

        with patch("modules.nasa_apod.requests.get", side_effect=[apod_resp, img_resp]) as mock_get:
            nasa_apod.generate(config)
        # second call is the image download; assert it hit the hdurl, not url
        assert mock_get.call_args_list[1][0][0] == "http://example.com/hd.jpg"

    def test_same_cache_and_output_path_skips_copy(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nasa_apod, "CACHE_DIR", str(tmp_path))
        cached_path = nasa_apod._today_cache_path()
        os.makedirs(os.path.dirname(cached_path), exist_ok=True)
        Image.new("RGB", (800, 480), "blue").save(cached_path)

        config = self._config(cached_path)
        with patch("modules.nasa_apod.shutil.copy2") as mock_copy:
            result = nasa_apod.generate(config)
            mock_copy.assert_not_called()
        assert result == cached_path


class TestDownloadImage:
    @patch("modules.nasa_apod.requests.get")
    def test_successful_download_returns_rgb_image(self, mock_get):
        import io
        buf = io.BytesIO()
        Image.new("RGB", (100, 100), "purple").save(buf, format="JPEG")
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.content = buf.getvalue()
        mock_get.return_value = resp

        img = nasa_apod._download_image("http://example.com/x.jpg")
        assert img is not None
        assert img.mode == "RGB"

    @patch("modules.nasa_apod.requests.get")
    def test_network_failure_returns_none(self, mock_get):
        mock_get.side_effect = Exception("timeout")
        assert nasa_apod._download_image("http://example.com/x.jpg") is None

    @patch("modules.nasa_apod.requests.get")
    def test_corrupt_image_bytes_returns_none(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.content = b"not an image"
        mock_get.return_value = resp
        assert nasa_apod._download_image("http://example.com/x.jpg") is None


class TestRenderImage:
    def test_output_matches_requested_canvas_size(self, tmp_path):
        img = Image.new("RGB", (1600, 900), "green")
        output_path = str(tmp_path / "out.bmp")
        nasa_apod._render_image(img, "A Title", output_path, width=800, height=480)
        result = Image.open(output_path)
        assert result.size == (800, 480)

    def test_empty_title_does_not_crash(self, tmp_path):
        img = Image.new("RGB", (800, 480), "black")
        output_path = str(tmp_path / "out.bmp")
        result = nasa_apod._render_image(img, "", output_path)
        assert os.path.exists(result)

    def test_very_long_title_does_not_crash(self, tmp_path):
        img = Image.new("RGB", (800, 480), "black")
        output_path = str(tmp_path / "out.bmp")
        title = "A very long astronomy picture title " * 10
        result = nasa_apod._render_image(img, title, output_path)
        assert os.path.exists(result)


class TestRenderTextFallback:
    def test_short_explanation_not_truncated(self, tmp_path):
        output_path = str(tmp_path / "out.bmp")
        nasa_apod._render_text_fallback("Title", "Short explanation.", output_path)
        assert os.path.exists(output_path)

    def test_long_explanation_truncated_with_ellipsis(self, tmp_path):
        output_path = str(tmp_path / "out.bmp")
        long_explanation = "word " * 100
        result = nasa_apod._render_text_fallback("Title", long_explanation, output_path)
        img = Image.open(result)
        assert img.size == (800, 480)

    def test_empty_explanation_does_not_crash(self, tmp_path):
        output_path = str(tmp_path / "out.bmp")
        result = nasa_apod._render_text_fallback("Video title", "", output_path)
        assert os.path.exists(result)


class TestErrorImageCustomMessage:
    def test_custom_message_used(self, tmp_path):
        output_path = str(tmp_path / "err.bmp")
        result = nasa_apod._error_image(output_path, message="Custom error")
        assert os.path.exists(result)
