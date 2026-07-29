"""
Unit tests for modules/forecast_graph.py: hourly-data slicing and the
cache-fallback logic in fetch_forecast().
"""

import sys
import os
import json
import time
from unittest.mock import patch, MagicMock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.forecast_graph import _slice_hours, fetch_forecast, _cache_path, HOURS


def _sample_config(tmp_path, lat=35.9, lon=-86.8):
    return {
        "forecast_graph": {"cache_dir": str(tmp_path)},
        "forecast_location": {"latitude": lat, "longitude": lon, "name": "Franklin, TN"},
    }


class TestSliceHours:
    def test_basic_slicing(self):
        data = {
            "hourly": {
                "time": ["2026-01-01T00:00", "2026-01-01T01:00"],
                "temperature_2m": [50, 52],
                "precipitation_probability": [10, 20],
            }
        }
        times, temps, probs = _slice_hours(data)
        assert times == ["2026-01-01T00:00", "2026-01-01T01:00"]
        assert temps == [50, 52]
        assert probs == [10, 20]

    def test_caps_at_hours_constant(self):
        n = HOURS + 20
        data = {
            "hourly": {
                "time": [f"h{i}" for i in range(n)],
                "temperature_2m": [50] * n,
                "precipitation_probability": [10] * n,
            }
        }
        times, temps, probs = _slice_hours(data)
        assert len(times) == HOURS
        assert len(temps) == HOURS

    def test_none_values_replaced_with_zero(self):
        data = {
            "hourly": {
                "time": ["h0", "h1"],
                "temperature_2m": [None, 55],
                "precipitation_probability": [None, None],
            }
        }
        times, temps, probs = _slice_hours(data)
        assert temps == [0, 55]
        assert probs == [0, 0]

    def test_missing_precip_probability_defaults_to_zero(self):
        data = {
            "hourly": {
                "time": ["h0", "h1"],
                "temperature_2m": [50, 51],
                # no precipitation_probability key at all
            }
        }
        times, temps, probs = _slice_hours(data)
        assert probs == [0, 0]

    def test_empty_hourly_returns_none(self):
        data = {"hourly": {"time": [], "temperature_2m": []}}
        assert _slice_hours(data) is None

    def test_missing_hourly_key_returns_none(self):
        assert _slice_hours({}) is None

    def test_mismatched_lengths_uses_minimum(self):
        data = {
            "hourly": {
                "time": ["h0", "h1", "h2"],
                "temperature_2m": [50, 51],  # shorter
                "precipitation_probability": [1, 2, 3],
            }
        }
        times, temps, probs = _slice_hours(data)
        assert len(times) == 2
        assert len(temps) == 2
        assert len(probs) == 2


class TestFetchForecast:
    def test_uses_fresh_cache_without_network_call(self, tmp_path):
        config = _sample_config(tmp_path)
        cache_file = _cache_path(config)
        cached_data = {"_fetched_at": time.time(), "hourly": {"time": ["h0"]}}
        with open(cache_file, "w") as f:
            json.dump(cached_data, f)

        with patch("modules.forecast_graph.requests.get") as mock_get:
            result = fetch_forecast(config)
            mock_get.assert_not_called()
        assert result == cached_data

    @patch("modules.forecast_graph.requests.get")
    def test_fetches_when_cache_expired(self, mock_get, tmp_path):
        config = _sample_config(tmp_path)
        cache_file = _cache_path(config)
        stale_data = {"_fetched_at": time.time() - 10000, "hourly": {"time": ["old"]}}
        with open(cache_file, "w") as f:
            json.dump(stale_data, f)

        fresh_json = {"hourly": {"time": ["new"]}}
        mock_resp = MagicMock()
        mock_resp.json.return_value = fresh_json
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        result = fetch_forecast(config)
        assert result["hourly"]["time"] == ["new"]
        assert "_fetched_at" in result
        mock_get.assert_called_once()

    @patch("modules.forecast_graph.requests.get")
    def test_falls_back_to_stale_cache_on_network_failure(self, mock_get, tmp_path):
        config = _sample_config(tmp_path)
        cache_file = _cache_path(config)
        stale_data = {"_fetched_at": time.time() - 10000, "hourly": {"time": ["old"]}}
        with open(cache_file, "w") as f:
            json.dump(stale_data, f)

        mock_get.side_effect = Exception("network down")

        result = fetch_forecast(config)
        assert result == stale_data

    @patch("modules.forecast_graph.requests.get")
    def test_returns_none_when_no_cache_and_network_fails(self, mock_get, tmp_path):
        config = _sample_config(tmp_path)
        mock_get.side_effect = Exception("network down")
        result = fetch_forecast(config)
        assert result is None

    def test_missing_coordinates_returns_cache_or_none(self, tmp_path):
        config = {
            "forecast_graph": {"cache_dir": str(tmp_path)},
            "forecast_location": {},
        }
        result = fetch_forecast(config)
        assert result is None

    def test_writes_cache_file_after_fresh_fetch(self, tmp_path):
        config = _sample_config(tmp_path)
        fresh_json = {"hourly": {"time": ["new"]}}
        mock_resp = MagicMock()
        mock_resp.json.return_value = fresh_json
        mock_resp.raise_for_status.return_value = None

        with patch("modules.forecast_graph.requests.get", return_value=mock_resp):
            fetch_forecast(config)

        cache_file = _cache_path(config)
        assert os.path.exists(cache_file)
        with open(cache_file) as f:
            saved = json.load(f)
        assert saved["hourly"]["time"] == ["new"]
