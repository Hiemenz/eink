"""
Unit tests for modules/aurora.py — Kp color/threshold logic, NOAA text-forecast
parsing, and cache/fetch fallback behavior (network calls mocked).
"""

import json
import os
import sys
import time
from unittest.mock import patch, MagicMock

import pytest
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules import aurora


class TestKpColor:
    def test_low(self):
        assert aurora._kp_color(0) == aurora.GREEN
        assert aurora._kp_color(2) == aurora.GREEN

    def test_moderate(self):
        assert aurora._kp_color(3) == aurora.YELLOW
        assert aurora._kp_color(4) == aurora.YELLOW

    def test_high(self):
        assert aurora._kp_color(5) == aurora.ORANGE
        assert aurora._kp_color(6) == aurora.ORANGE

    def test_extreme(self):
        assert aurora._kp_color(7) == aurora.RED
        assert aurora._kp_color(9) == aurora.RED


class TestKpThreshold:
    def test_high_latitude_low_threshold(self):
        assert aurora._kp_threshold(67) == 1

    def test_mid_latitude(self):
        assert aurora._kp_threshold(50) == 5

    def test_low_latitude_high_threshold(self):
        assert aurora._kp_threshold(20) == 9

    def test_negative_latitude_uses_abs(self):
        assert aurora._kp_threshold(-67) == 1

    def test_boundary_exact_match(self):
        assert aurora._kp_threshold(60) == 2


class TestCache:
    def test_load_missing_cache(self, tmp_path):
        assert aurora._load_cache(str(tmp_path)) is None

    def test_save_and_load(self, tmp_path):
        cache_dir = str(tmp_path)
        aurora._save_cache(cache_dir, {"current_kp": 3.0, "fetched_at": time.time()})
        data = aurora._load_cache(cache_dir)
        assert data["current_kp"] == 3.0

    def test_load_corrupt_cache(self, tmp_path):
        path = tmp_path / "aurora_cache.json"
        path.write_text("{broken")
        assert aurora._load_cache(str(tmp_path)) is None


class TestFetchCurrentKp:
    def test_list_of_lists_shape(self):
        rows = [["time_tag", "Kp", "a_running", "station_count"], ["2026-07-21 00:00:00", "3.33", "12", "13"]]
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = rows
        with patch.object(aurora.requests, "get", return_value=mock_resp):
            kp = aurora._fetch_current_kp()
        assert kp == 3.33

    def test_list_of_dicts_shape(self):
        rows = [{"Kp": "1.0"}, {"Kp": "5.67"}]
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = rows
        with patch.object(aurora.requests, "get", return_value=mock_resp):
            kp = aurora._fetch_current_kp()
        assert kp == 5.67

    def test_network_failure_returns_none(self):
        with patch.object(aurora.requests, "get", side_effect=Exception("timeout")):
            assert aurora._fetch_current_kp() is None

    def test_too_short_list_returns_none(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = [["header"]]
        with patch.object(aurora.requests, "get", return_value=mock_resp):
            assert aurora._fetch_current_kp() is None

    def test_non_list_returns_none(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"error": "bad"}
        with patch.object(aurora.requests, "get", return_value=mock_resp):
            assert aurora._fetch_current_kp() is None

    def test_malformed_row_returns_none(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = [["header"], {"unexpected": "shape"}]
        with patch.object(aurora.requests, "get", return_value=mock_resp):
            # dict without "Kp" key -> KeyError caught -> None
            assert aurora._fetch_current_kp() is None


class TestParseForecastText:
    SAMPLE = """NOAA Kp index breakdown Jul 21-Jul 23

                  Jul 21       Jul 22       Jul 23
00-03UT        3.00         2.67         2.33
03-06UT        2.67         2.33         4.00
06-09UT        2.00         2.00         2.00
Some trailing description line not part of the table
"""

    def test_parses_three_days_max_kp(self):
        result = aurora._parse_forecast_text(self.SAMPLE)
        assert result is not None
        assert len(result) == 3
        assert result[0]["day"] == "Jul 21"
        assert result[0]["kp"] == 3.00
        assert result[1]["kp"] == 2.67
        assert result[2]["kp"] == 4.00

    def test_missing_breakdown_header_returns_none(self):
        assert aurora._parse_forecast_text("no relevant data here") is None

    def test_missing_day_labels_returns_none(self):
        text = "NOAA Kp index breakdown Jul 21-Jul 23\nno day header row here\n"
        assert aurora._parse_forecast_text(text) is None

    def test_all_zero_values_returns_none(self):
        text = (
            "NOAA Kp index breakdown Jul 21-Jul 23\n"
            "              Jul 21       Jul 22       Jul 23\n"
        )
        assert aurora._parse_forecast_text(text) is None


class TestFetchForecast:
    def test_network_failure_returns_none(self):
        with patch.object(aurora.requests, "get", side_effect=Exception("timeout")):
            assert aurora._fetch_forecast() is None

    def test_parse_exception_returns_none(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.text = "irrelevant text"
        with patch.object(aurora.requests, "get", return_value=mock_resp), \
             patch.object(aurora, "_parse_forecast_text", side_effect=Exception("parse error")):
            assert aurora._fetch_forecast() is None


class TestGetData:
    def test_fresh_cache_skips_fetch(self, tmp_path):
        cache_dir = str(tmp_path)
        aurora._save_cache(cache_dir, {"current_kp": 2.0, "forecast": None, "fetched_at": time.time()})
        with patch.object(aurora, "_fetch_current_kp") as mock_fetch:
            data = aurora._get_data(cache_dir)
        mock_fetch.assert_not_called()
        assert data["current_kp"] == 2.0

    def test_stale_cache_triggers_fetch(self, tmp_path):
        cache_dir = str(tmp_path)
        aurora._save_cache(cache_dir, {"current_kp": 2.0, "forecast": None, "fetched_at": 0})
        with patch.object(aurora, "_fetch_current_kp", return_value=5.0), \
             patch.object(aurora, "_fetch_forecast", return_value=None):
            data = aurora._get_data(cache_dir)
        assert data["current_kp"] == 5.0

    def test_fetch_fails_falls_back_to_stale_cache(self, tmp_path):
        cache_dir = str(tmp_path)
        aurora._save_cache(cache_dir, {"current_kp": 2.0, "forecast": None, "fetched_at": 0})
        with patch.object(aurora, "_fetch_current_kp", return_value=None):
            data = aurora._get_data(cache_dir)
        assert data["current_kp"] == 2.0

    def test_no_cache_no_fetch_returns_none(self, tmp_path):
        cache_dir = str(tmp_path)
        with patch.object(aurora, "_fetch_current_kp", return_value=None):
            data = aurora._get_data(cache_dir)
        assert data is None


class TestFitFont:
    def test_shrinks_to_fit_narrow_width(self):
        img = Image.new("RGB", (800, 480))
        draw = ImageDraw.Draw(img)
        long_text = "This verdict text is much too long to fit at full size"
        font = aurora._fit_font(draw, long_text, max_w=100, start_size=40, config={})
        assert aurora._text_w(draw, long_text, font) <= aurora._text_w(
            draw, long_text, aurora.get_font(40, bold=True, config={})
        )

    def test_returns_start_size_when_text_already_fits(self):
        img = Image.new("RGB", (800, 480))
        draw = ImageDraw.Draw(img)
        font = aurora._fit_font(draw, "Hi", max_w=800, start_size=40, config={})
        assert aurora._text_w(draw, "Hi", font) <= 800

    def test_never_shrinks_below_min_size(self):
        img = Image.new("RGB", (800, 480))
        draw = ImageDraw.Draw(img)
        # Even an absurdly long string must bottom out at min_size, not loop forever.
        font = aurora._fit_font(draw, "X" * 500, max_w=10, start_size=40, config={}, min_size=14)
        assert font is not None


class TestGenerate:
    def _config(self, out, latitude=60.0):
        return {
            "aurora": {"output_path": out, "cache_dir": os.path.dirname(out) or "."},
            "forecast_location": {"latitude": latitude, "name": "Test City"},
        }

    def test_unavailable_when_no_data(self, tmp_path):
        out = str(tmp_path / "aurora.bmp")
        with patch.object(aurora, "_get_data", return_value=None):
            result = aurora.generate(self._config(out))
        assert result == out
        img = Image.open(out)
        assert img.size == (aurora.WIDTH, aurora.HEIGHT)

    def test_renders_with_data_no_forecast(self, tmp_path):
        out = str(tmp_path / "aurora.bmp")
        data = {"current_kp": 3.0, "forecast": None, "fetched_at": time.time()}
        with patch.object(aurora, "_get_data", return_value=data):
            result = aurora.generate(self._config(out))
        assert result == out
        assert os.path.exists(out)

    def test_renders_with_three_day_forecast(self, tmp_path):
        out = str(tmp_path / "aurora.bmp")
        data = {
            "current_kp": 5.0,
            "forecast": [
                {"day": "Jul 21", "kp": 3.0},
                {"day": "Jul 22", "kp": 5.0},
                {"day": "Jul 23", "kp": 2.0},
            ],
            "fetched_at": time.time(),
        }
        with patch.object(aurora, "_get_data", return_value=data):
            result = aurora.generate(self._config(out))
        assert result == out
        assert os.path.exists(out)

    def test_high_latitude_low_kp_marked_visible(self, tmp_path):
        # threshold for lat=67 is 1, so even Kp=2 should read as "may be visible".
        out = str(tmp_path / "aurora.bmp")
        data = {"current_kp": 2.0, "forecast": None, "fetched_at": time.time()}
        with patch.object(aurora, "_get_data", return_value=data):
            result = aurora.generate(self._config(out, latitude=67.0))
        assert os.path.exists(result)

    def test_low_latitude_low_kp_marked_not_visible(self, tmp_path):
        out = str(tmp_path / "aurora.bmp")
        data = {"current_kp": 2.0, "forecast": None, "fetched_at": time.time()}
        with patch.object(aurora, "_get_data", return_value=data):
            result = aurora.generate(self._config(out, latitude=20.0))
        assert os.path.exists(result)

    def test_missing_fetched_at_renders_unknown_footer(self, tmp_path):
        out = str(tmp_path / "aurora.bmp")
        data = {"current_kp": 3.0, "forecast": None, "fetched_at": 0}
        with patch.object(aurora, "_get_data", return_value=data):
            result = aurora.generate(self._config(out))
        assert os.path.exists(result)

    def test_creates_output_directory(self, tmp_path):
        out = str(tmp_path / "nested" / "dir" / "aurora.bmp")
        with patch.object(aurora, "_get_data", return_value=None):
            result = aurora.generate(self._config(out))
        assert os.path.exists(result)

    def test_default_latitude_when_missing_location(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch.object(aurora, "_get_data", return_value=None):
            result = aurora.generate({"aurora": {"output_path": "aurora.bmp"}})
        assert os.path.exists(result)
