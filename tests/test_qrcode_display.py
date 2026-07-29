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
