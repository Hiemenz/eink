"""
Unit tests for modules/qrcode_display.py.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules import qrcode_display as qd


class TestBuildQrText:
    def test_wifi_config_takes_priority(self):
        cfg = {
            "wifi_ssid": "MyNetwork",
            "wifi_password": "secret123",
            "wifi_security": "WPA",
            "text": "should be ignored",
        }
        text = qd._build_qr_text(cfg)
        assert text == "WIFI:T:WPA;S:MyNetwork;P:secret123;;"

    def test_wifi_defaults_security_to_wpa(self):
        cfg = {"wifi_ssid": "MyNetwork", "wifi_password": "pw"}
        text = qd._build_qr_text(cfg)
        assert text == "WIFI:T:WPA;S:MyNetwork;P:pw;;"

    def test_wifi_open_network_empty_password(self):
        cfg = {"wifi_ssid": "OpenNet", "wifi_security": "nopass"}
        text = qd._build_qr_text(cfg)
        assert text == "WIFI:T:nopass;S:OpenNet;P:;;"

    def test_plain_text_used_when_no_ssid(self):
        cfg = {"text": "https://example.com"}
        assert qd._build_qr_text(cfg) == "https://example.com"

    def test_whitespace_only_ssid_falls_back_to_text(self):
        cfg = {"wifi_ssid": "   ", "text": "hello"}
        assert qd._build_qr_text(cfg) == "hello"

    def test_no_config_returns_none(self):
        assert qd._build_qr_text({}) is None

    def test_whitespace_only_text_returns_none(self):
        assert qd._build_qr_text({"text": "   "}) is None

    def test_text_is_stripped(self):
        cfg = {"text": "  padded text  "}
        assert qd._build_qr_text(cfg) == "padded text"


class TestMakeQrImage:
    def test_returns_rgb_image(self):
        img = qd._make_qr_image("hello world")
        assert img.mode == "RGB"
        assert img.width > 0 and img.height > 0

    def test_unicode_text_encodes_without_error(self):
        img = qd._make_qr_image("café ☕ 日本語")
        assert img.mode == "RGB"

    def test_very_long_text_raises(self):
        # QR version 40 (max) with ERROR_CORRECT_H tops out well under 2000
        # alphanumeric/byte chars -- this should exceed capacity and raise.
        with pytest.raises(ValueError):
            qd._make_qr_image("x" * 5000)


class TestGenerateIntegration:
    def test_generate_placeholder_when_unconfigured(self, tmp_path):
        output_path = str(tmp_path / "out.bmp")
        result = qd.generate({"qrcode_display": {"output_path": output_path}})
        assert result == output_path
        assert os.path.exists(output_path)

    def test_generate_renders_qr_when_text_configured(self, tmp_path):
        output_path = str(tmp_path / "out.bmp")
        result = qd.generate({
            "qrcode_display": {"output_path": output_path, "text": "hello", "label": "Scan me"}
        })
        assert result == output_path
        assert os.path.exists(output_path)

    def test_generate_handles_non_dict_config_section(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = qd.generate({"qrcode_display": None})
        assert os.path.exists(result)

    def test_generate_missing_section_uses_default_output_path(self, tmp_path, monkeypatch):
        """No qrcode_display key at all in config -- falls back to images/qrcode_display.bmp."""
        monkeypatch.chdir(tmp_path)
        result = qd.generate({})
        assert result == "images/qrcode_display.bmp"
        assert os.path.exists(result)

    def test_generate_sublabel_without_label(self, tmp_path):
        """Sublabel-only branch: cursor_y math must not reference an unset label height."""
        output_path = str(tmp_path / "out.bmp")
        result = qd.generate({
            "qrcode_display": {"output_path": output_path, "text": "hello", "sublabel": "small print"}
        })
        assert os.path.exists(result)

    def test_generate_label_and_sublabel_together(self, tmp_path):
        output_path = str(tmp_path / "out.bmp")
        result = qd.generate({
            "qrcode_display": {
                "output_path": output_path,
                "text": "hello",
                "label": "Scan me",
                "sublabel": "small print",
            }
        })
        assert os.path.exists(result)

    def test_generate_wifi_missing_password_key(self, tmp_path):
        """wifi_ssid set but wifi_password entirely absent -- must not KeyError."""
        output_path = str(tmp_path / "out.bmp")
        result = qd.generate({
            "qrcode_display": {"output_path": output_path, "wifi_ssid": "MyNet"}
        })
        assert os.path.exists(result)

    def test_generate_creates_output_directory(self, tmp_path):
        output_path = str(tmp_path / "nested" / "dir" / "out.bmp")
        result = qd.generate({"qrcode_display": {"output_path": output_path, "text": "hi"}})
        assert result == output_path
        assert os.path.exists(output_path)

    def test_generate_image_has_expected_canvas_size(self, tmp_path):
        output_path = str(tmp_path / "out.bmp")
        qd.generate({"qrcode_display": {"output_path": output_path, "text": "hi"}})
        from PIL import Image
        img = Image.open(output_path)
        assert img.size == (qd.CANVAS_W, qd.CANVAS_H)
