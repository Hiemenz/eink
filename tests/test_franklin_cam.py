"""
Unit tests for modules/franklin_cam.py: multi-URL snapshot fetch
fallback logic and the resize/crop render geometry.
"""

import sys
import os
import io
from unittest.mock import patch, MagicMock

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.franklin_cam import _fetch_snapshot, _render, _error_image, generate


def _jpeg_bytes(w=640, h=480, color="red"):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="JPEG")
    return buf.getvalue()


class TestFetchSnapshot:
    @patch("modules.franklin_cam.requests.get")
    def test_alias_url_success_returns_image(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.headers = {"Content-Type": "image/jpeg"}
        resp.content = _jpeg_bytes()
        mock_get.return_value = resp

        img = _fetch_snapshot("myalias", "", "")
        assert img is not None
        assert img.mode == "RGB"
        mock_get.assert_called_once()
        assert "alias=myalias" in mock_get.call_args[0][0]

    @patch("modules.franklin_cam.requests.get")
    def test_non_image_content_type_falls_through_to_next_url(self, mock_get):
        bad_resp = MagicMock()
        bad_resp.raise_for_status.return_value = None
        bad_resp.headers = {"Content-Type": "text/html"}

        good_resp = MagicMock()
        good_resp.raise_for_status.return_value = None
        good_resp.headers = {"Content-Type": "image/jpeg"}
        good_resp.content = _jpeg_bytes()

        mock_get.side_effect = [bad_resp, good_resp, good_resp]

        img = _fetch_snapshot("alias1", "host.example.com", "stream123")
        assert img is not None
        assert mock_get.call_count >= 2

    @patch("modules.franklin_cam.requests.get")
    def test_all_urls_fail_returns_none(self, mock_get):
        mock_get.side_effect = Exception("connection refused")
        img = _fetch_snapshot("alias1", "host.example.com", "stream123")
        assert img is None

    @patch("modules.franklin_cam.requests.get")
    def test_no_alias_or_stream_returns_none_without_request(self, mock_get):
        img = _fetch_snapshot("", "", "")
        assert img is None
        mock_get.assert_not_called()

    @patch("modules.franklin_cam.requests.get")
    def test_tries_https_then_http_stream_urls(self, mock_get):
        mock_get.side_effect = Exception("fail")
        _fetch_snapshot("", "host.example.com", "stream123")
        called_urls = [c[0][0] for c in mock_get.call_args_list]
        assert any(u.startswith("https://") for u in called_urls)
        assert any(u.startswith("http://") for u in called_urls)


class TestRender:
    def test_output_matches_requested_canvas_size(self, tmp_path):
        img = Image.new("RGB", (1920, 1080), "blue")
        output_path = str(tmp_path / "out.bmp")
        _render(img, "Test Cam", output_path, width=800, height=480)
        result = Image.open(output_path)
        assert result.size == (800, 480)

    def test_crops_to_cover_canvas_from_tall_source(self, tmp_path):
        # A tall, narrow source image should still fill an 800x480 canvas.
        img = Image.new("RGB", (400, 1200), "green")
        output_path = str(tmp_path / "out.bmp")
        result_path = _render(img, "Label", output_path, width=800, height=480)
        result = Image.open(result_path)
        assert result.size == (800, 480)

    def test_file_saved_at_output_path(self, tmp_path):
        img = Image.new("RGB", (800, 480), "white")
        output_path = str(tmp_path / "nested" / "out.bmp")
        result = _render(img, "Label", output_path)
        assert result == output_path
        assert os.path.exists(output_path)


class TestErrorImage:
    def test_creates_placeholder_image_at_requested_size(self, tmp_path):
        output_path = str(tmp_path / "err.bmp")
        result = _error_image(output_path, width=800, height=480)
        assert result == output_path
        img = Image.open(output_path)
        assert img.size == (800, 480)


class TestGenerateFallback:
    @patch("modules.franklin_cam.requests.get")
    def test_generate_falls_back_to_error_image_when_fetch_fails(self, mock_get, tmp_path):
        mock_get.side_effect = Exception("no network")
        output_path = str(tmp_path / "out.bmp")
        config = {"franklin_cam": {"output_path": output_path}, "width": 800, "height": 480}
        result = generate(config)
        assert result == output_path
        assert os.path.exists(output_path)
