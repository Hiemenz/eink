"""
Unit tests for modules/carbon_intensity.py — tier/color mapping, provider
fallback logic, cleanest-window analysis, and cache TTL (network mocked).
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

from modules import carbon_intensity as ci


class TestCarbonColor:
    def test_very_clean(self):
        assert ci._carbon_color(50) == (0, 255, 0)

    def test_low(self):
        assert ci._carbon_color(150) == (0, 255, 0)

    def test_moderate(self):
        assert ci._carbon_color(250) == (255, 255, 0)

    def test_high(self):
        assert ci._carbon_color(350) == (255, 128, 0)

    def test_very_high(self):
        assert ci._carbon_color(500) == (255, 0, 0)


class TestCarbonVerdict:
    def test_very_clean(self):
        assert ci._carbon_verdict(50) == "VERY CLEAN"

    def test_low(self):
        assert ci._carbon_verdict(150) == "LOW"

    def test_moderate(self):
        assert ci._carbon_verdict(250) == "MODERATE"

    def test_high(self):
        assert ci._carbon_verdict(350) == "HIGH"

    def test_very_high(self):
        assert ci._carbon_verdict(500) == "VERY HIGH"


class TestCache:
    def test_load_missing(self, tmp_path):
        assert ci._load_cache(str(tmp_path)) is None

    def test_save_and_load(self, tmp_path):
        cache_dir = str(tmp_path)
        ci._save_cache(cache_dir, {"intensity": 120.0, "fetched_at": time.time()})
        data = ci._load_cache(cache_dir)
        assert data["intensity"] == 120.0

    def test_load_corrupt(self, tmp_path):
        path = tmp_path / "carbon_cache.json"
        path.write_text("{not valid json")
        assert ci._load_cache(str(tmp_path)) is None


class TestFetchUk:
    def test_success_with_actual_value(self):
        current_resp = MagicMock()
        current_resp.raise_for_status.return_value = None
        current_resp.json.return_value = {
            "data": [{"intensity": {"actual": 123, "forecast": 130, "index": "moderate"}}]
        }
        forecast_resp = MagicMock()
        forecast_resp.raise_for_status.return_value = None
        forecast_resp.json.return_value = {
            "data": [
                {"intensity": {"forecast": 100}, "from": "2026-07-21T00:00Z"},
                {"intensity": {"forecast": 110}, "from": "2026-07-21T00:30Z"},
            ]
        }
        with patch.object(ci.requests, "get", side_effect=[current_resp, forecast_resp]):
            data = ci._fetch_uk()
        assert data["intensity"] == 123.0
        assert data["index"] == "moderate"
        assert len(data["forecast"]) == 2

    def test_falls_back_to_forecast_value_when_no_actual(self):
        current_resp = MagicMock()
        current_resp.raise_for_status.return_value = None
        current_resp.json.return_value = {
            "data": [{"intensity": {"actual": None, "forecast": 200, "index": "high"}}]
        }
        forecast_resp = MagicMock()
        forecast_resp.raise_for_status.return_value = None
        forecast_resp.json.return_value = {"data": []}
        with patch.object(ci.requests, "get", side_effect=[current_resp, forecast_resp]):
            data = ci._fetch_uk()
        assert data["intensity"] == 200.0

    def test_current_fetch_failure_returns_none(self):
        with patch.object(ci.requests, "get", side_effect=Exception("timeout")):
            assert ci._fetch_uk() is None

    def test_no_actual_or_forecast_returns_none(self):
        current_resp = MagicMock()
        current_resp.raise_for_status.return_value = None
        current_resp.json.return_value = {
            "data": [{"intensity": {"actual": None, "forecast": None, "index": ""}}]
        }
        with patch.object(ci.requests, "get", return_value=current_resp):
            assert ci._fetch_uk() is None

    def test_forecast_fetch_failure_still_returns_current(self):
        current_resp = MagicMock()
        current_resp.raise_for_status.return_value = None
        current_resp.json.return_value = {
            "data": [{"intensity": {"actual": 90, "forecast": 95, "index": "low"}}]
        }
        with patch.object(
            ci.requests, "get", side_effect=[current_resp, Exception("forecast down")]
        ):
            data = ci._fetch_uk()
        assert data["intensity"] == 90.0
        assert data["forecast"] == []


class TestFetchElectricityMaps:
    def test_success(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"carbonIntensity": 250, "zone": "US-TEN-TVA"}
        with patch.object(ci.requests, "get", return_value=mock_resp):
            data = ci._fetch_electricitymaps("US-TEN-TVA", "token123")
        assert data["intensity"] == 250.0
        assert data["region"] == "US-TEN-TVA"
        assert data["provider"] == "ElectricityMaps"

    def test_missing_field_returns_none(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"zone": "US-TEN-TVA"}
        with patch.object(ci.requests, "get", return_value=mock_resp):
            assert ci._fetch_electricitymaps("US-TEN-TVA", "token123") is None

    def test_network_failure_returns_none(self):
        with patch.object(ci.requests, "get", side_effect=Exception("timeout")):
            assert ci._fetch_electricitymaps("US-TEN-TVA", "token123") is None


class TestGetCarbonData:
    def test_fresh_cache_skips_fetch(self, tmp_path):
        cache_dir = str(tmp_path)
        ci._save_cache(cache_dir, {"intensity": 100.0, "region": "UK", "fetched_at": time.time()})
        with patch.object(ci, "_fetch_uk") as mock_fetch:
            data = ci._get_carbon_data({"provider": "uk"}, cache_dir)
        mock_fetch.assert_not_called()
        assert data["intensity"] == 100.0

    def test_electricitymaps_without_token_falls_back_to_uk(self, tmp_path):
        cache_dir = str(tmp_path)
        with patch.object(ci, "_fetch_uk", return_value={"intensity": 80.0, "region": "UK", "fetched_at": time.time()}) as mock_uk, \
             patch.object(ci, "_fetch_electricitymaps") as mock_em:
            data = ci._get_carbon_data({"provider": "electricitymaps", "em_token": "", "em_zone": ""}, cache_dir)
        mock_em.assert_not_called()
        mock_uk.assert_called_once()
        assert data["intensity"] == 80.0

    def test_electricitymaps_with_token_uses_em(self, tmp_path):
        cache_dir = str(tmp_path)
        with patch.object(ci, "_fetch_electricitymaps", return_value={"intensity": 300.0, "region": "TVA", "fetched_at": time.time()}) as mock_em, \
             patch.object(ci, "_fetch_uk") as mock_uk:
            data = ci._get_carbon_data(
                {"provider": "electricitymaps", "em_token": "tok", "em_zone": "US-TEN-TVA"}, cache_dir
            )
        mock_uk.assert_not_called()
        assert data["intensity"] == 300.0

    def test_fetch_fails_falls_back_to_stale_cache(self, tmp_path):
        cache_dir = str(tmp_path)
        ci._save_cache(cache_dir, {"intensity": 50.0, "region": "UK", "fetched_at": 0})
        with patch.object(ci, "_fetch_uk", return_value=None):
            data = ci._get_carbon_data({"provider": "uk"}, cache_dir)
        assert data["intensity"] == 50.0

    def test_no_cache_no_fetch_returns_none(self, tmp_path):
        cache_dir = str(tmp_path)
        with patch.object(ci, "_fetch_uk", return_value=None):
            data = ci._get_carbon_data({"provider": "uk"}, cache_dir)
        assert data is None


class TestCleanestWindow:
    def test_finds_lowest_average_window(self):
        forecast = [
            {"value": 300, "from": "2026-07-21T00:00Z"},
            {"value": 300, "from": "2026-07-21T00:30Z"},
            {"value": 50, "from": "2026-07-21T01:00Z"},
            {"value": 50, "from": "2026-07-21T01:30Z"},
            {"value": 300, "from": "2026-07-21T02:00Z"},
        ]
        result = ci._cleanest_window(forecast, span=2)
        assert result is not None
        assert "cleanest window" in result

    def test_too_short_returns_none(self):
        forecast = [{"value": 100, "from": "2026-07-21T00:00Z"}]
        assert ci._cleanest_window(forecast, span=4) is None

    def test_bad_timestamps_return_none(self):
        forecast = [{"value": 100, "from": "garbage"} for _ in range(4)]
        assert ci._cleanest_window(forecast, span=4) is None
