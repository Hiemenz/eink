"""
Unit tests for modules/air_quality.py — AQI tier/color mapping, cache TTL,
and AirNow fetch/parsing logic (network calls mocked).
"""

import json
import os
import sys
import time
from unittest.mock import patch, MagicMock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules import air_quality


class TestAqiColor:
    def test_good(self):
        assert air_quality._aqi_color(25) == (0, 255, 0)

    def test_moderate(self):
        assert air_quality._aqi_color(75) == (255, 255, 0)

    def test_usg(self):
        assert air_quality._aqi_color(120) == (255, 128, 0)

    def test_unhealthy(self):
        assert air_quality._aqi_color(180) == (255, 0, 0)

    def test_very_unhealthy(self):
        assert air_quality._aqi_color(250) == (150, 0, 150)

    def test_hazardous(self):
        assert air_quality._aqi_color(350) == (0, 0, 0)

    def test_boundary_50(self):
        assert air_quality._aqi_color(50) == (0, 255, 0)

    def test_boundary_51(self):
        assert air_quality._aqi_color(51) == (255, 255, 0)

    def test_extreme_high(self):
        assert air_quality._aqi_color(999) == (0, 0, 0)

    def test_all_tier_boundaries(self):
        # (aqi, expected_color) at every tier transition
        cases = [
            (100, (255, 255, 0)), (101, (255, 128, 0)),
            (150, (255, 128, 0)), (151, (255, 0, 0)),
            (200, (255, 0, 0)), (201, (150, 0, 150)),
            (300, (150, 0, 150)), (301, (0, 0, 0)),
        ]
        for aqi, expected in cases:
            assert air_quality._aqi_color(aqi) == expected, f"aqi={aqi}"

    def test_zero_is_good(self):
        assert air_quality._aqi_color(0) == (0, 255, 0)


class TestAqiCategory:
    def test_good(self):
        assert air_quality._aqi_category(10) == "Good"

    def test_moderate(self):
        assert air_quality._aqi_category(80) == "Moderate"

    def test_hazardous(self):
        assert air_quality._aqi_category(500) == "Hazardous"


class TestCache:
    def test_load_cache_missing(self, tmp_path):
        assert air_quality._load_cache(str(tmp_path)) is None

    def test_save_and_load_cache(self, tmp_path):
        cache_dir = str(tmp_path)
        payload = {"aqi": 42, "category": "Good", "fetched_at": time.time()}
        air_quality._save_cache(cache_dir, payload)
        loaded = air_quality._load_cache(cache_dir)
        assert loaded["aqi"] == 42

    def test_load_cache_corrupt(self, tmp_path):
        path = tmp_path / "aqi_cache.json"
        path.write_text("not json{")
        assert air_quality._load_cache(str(tmp_path)) is None


class TestFetchAqi:
    def test_success_prefers_pm25(self):
        entries = [
            {"ParameterName": "OZONE", "AQI": 40, "Category": {"Name": "Good"}},
            {"ParameterName": "PM2.5", "AQI": 80, "Category": {"Name": "Moderate"}},
        ]
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = entries
        with patch.object(air_quality.requests, "get", return_value=mock_resp):
            data = air_quality._fetch_aqi("37064", "fakekey")
        assert data["aqi"] == 80
        assert data["parameter"] == "PM2.5"
        assert data["category"] == "Moderate"

    def test_falls_back_to_first_entry_if_no_pm25(self):
        entries = [{"ParameterName": "OZONE", "AQI": 40, "Category": {"Name": "Good"}}]
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = entries
        with patch.object(air_quality.requests, "get", return_value=mock_resp):
            data = air_quality._fetch_aqi("37064", "fakekey")
        assert data["aqi"] == 40
        assert data["parameter"] == "OZONE"

    def test_network_failure_returns_none(self):
        with patch.object(air_quality.requests, "get", side_effect=Exception("timeout")):
            assert air_quality._fetch_aqi("37064", "fakekey") is None

    def test_http_error_returns_none(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("500")
        with patch.object(air_quality.requests, "get", return_value=mock_resp):
            assert air_quality._fetch_aqi("37064", "fakekey") is None

    def test_empty_list_returns_none(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = []
        with patch.object(air_quality.requests, "get", return_value=mock_resp):
            assert air_quality._fetch_aqi("37064", "fakekey") is None

    def test_non_list_response_returns_none(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"error": "bad request"}
        with patch.object(air_quality.requests, "get", return_value=mock_resp):
            assert air_quality._fetch_aqi("37064", "fakekey") is None

    def test_missing_aqi_field_returns_none(self):
        entries = [{"ParameterName": "PM2.5", "Category": {"Name": "Good"}}]
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = entries
        with patch.object(air_quality.requests, "get", return_value=mock_resp):
            assert air_quality._fetch_aqi("37064", "fakekey") is None

    def test_category_falls_back_to_computed_value(self):
        # No Category field present at all -> derived from _aqi_category().
        entries = [{"ParameterName": "PM2.5", "AQI": 30}]
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = entries
        with patch.object(air_quality.requests, "get", return_value=mock_resp):
            data = air_quality._fetch_aqi("37064", "fakekey")
        assert data["category"] == "Good"


class TestGetAqiData:
    def test_uses_fresh_cache_without_fetching(self, tmp_path):
        cache_dir = str(tmp_path)
        air_quality._save_cache(cache_dir, {"aqi": 5, "category": "Good", "fetched_at": time.time()})
        with patch.object(air_quality, "_fetch_aqi") as mock_fetch:
            data = air_quality._get_aqi_data("37064", "key", cache_dir)
        mock_fetch.assert_not_called()
        assert data["aqi"] == 5

    def test_stale_cache_triggers_fetch(self, tmp_path):
        cache_dir = str(tmp_path)
        air_quality._save_cache(cache_dir, {"aqi": 5, "category": "Good", "fetched_at": 0})
        with patch.object(
            air_quality, "_fetch_aqi",
            return_value={"aqi": 99, "category": "Hazardous", "parameter": "PM2.5", "fetched_at": time.time()},
        ):
            data = air_quality._get_aqi_data("37064", "key", cache_dir)
        assert data["aqi"] == 99

    def test_fetch_fails_falls_back_to_stale_cache(self, tmp_path):
        cache_dir = str(tmp_path)
        air_quality._save_cache(cache_dir, {"aqi": 5, "category": "Good", "fetched_at": 0})
        with patch.object(air_quality, "_fetch_aqi", return_value=None):
            data = air_quality._get_aqi_data("37064", "key", cache_dir)
        assert data["aqi"] == 5

    def test_no_cache_no_fetch_success_returns_none(self, tmp_path):
        cache_dir = str(tmp_path)
        with patch.object(air_quality, "_fetch_aqi", return_value=None):
            data = air_quality._get_aqi_data("37064", "key", cache_dir)
        assert data is None


class TestGenerate:
    def _config(self, tmp_path, **overrides):
        cfg = {"air_quality": {
            "output_path": str(tmp_path / "aqi.bmp"),
            "cache_dir": str(tmp_path / "cache"),
            "zip_code": "37064",
            **overrides,
        }}
        return cfg

    def test_no_api_key_renders_unavailable(self, tmp_path):
        from PIL import Image
        config = self._config(tmp_path, api_key="")
        output = air_quality.generate(config)
        assert os.path.exists(output)
        img = Image.open(output)
        assert img.size == (800, 480)

    def test_data_unavailable_renders_unavailable_screen(self, tmp_path):
        config = self._config(tmp_path, api_key="fakekey")
        with patch.object(air_quality, "_get_aqi_data", return_value=None):
            output = air_quality.generate(config)
        assert os.path.exists(output)

    def test_with_data_renders_full_display(self, tmp_path):
        from PIL import Image
        config = self._config(tmp_path, api_key="fakekey")
        data = {"aqi": 42, "category": "Good", "parameter": "PM2.5", "fetched_at": time.time()}
        with patch.object(air_quality, "_get_aqi_data", return_value=data):
            output = air_quality.generate(config)
        assert os.path.exists(output)
        img = Image.open(output)
        assert img.size == (800, 480)

    def test_calls_get_aqi_data_with_configured_zip_and_key(self, tmp_path):
        config = self._config(tmp_path, api_key="fakekey", zip_code="90210")
        with patch.object(air_quality, "_get_aqi_data", return_value=None) as mock_get:
            air_quality.generate(config)
        args, _ = mock_get.call_args
        assert args[0] == "90210"
        assert args[1] == "fakekey"

    def test_creates_output_and_cache_directories(self, tmp_path):
        nested_output = tmp_path / "nested" / "out" / "aqi.bmp"
        nested_cache = tmp_path / "nested" / "cache"
        config = {"air_quality": {
            "output_path": str(nested_output),
            "cache_dir": str(nested_cache),
            "zip_code": "37064",
            "api_key": "",
        }}
        air_quality.generate(config)
        assert nested_output.exists()
        assert nested_cache.is_dir()
