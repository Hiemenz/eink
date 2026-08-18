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

from modules.forecast_graph import (
    _slice_hours,
    fetch_forecast,
    _cache_path,
    _read_cache,
    _render_unavailable,
    render_graph,
    generate,
    HOURS,
    WIDTH,
    HEIGHT,
)


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


class TestReadCache:
    def test_missing_file_returns_none(self, tmp_path):
        assert _read_cache(str(tmp_path / "missing.json")) is None

    def test_corrupt_file_returns_none(self, tmp_path):
        path = tmp_path / "corrupt.json"
        path.write_text("{not valid json")
        assert _read_cache(str(path)) is None


def _two_hour_data():
    return {
        "hourly": {
            "time": ["2026-01-01T00:00", "2026-01-01T01:00"],
            "temperature_2m": [40, 55],
            "precipitation_probability": [10, 80],
        }
    }


class TestRenderUnavailable:
    def test_creates_image_of_correct_size(self, tmp_path):
        output = str(tmp_path / "out.bmp")
        result = _render_unavailable(output)
        assert result == output
        from PIL import Image
        assert Image.open(output).size == (WIDTH, HEIGHT)


class TestRenderGraph:
    def test_renders_correct_size_image(self, tmp_path):
        output = str(tmp_path / "graph.bmp")
        config = {"forecast_location": {"name": "Franklin, TN"}}
        result = render_graph(config, _two_hour_data(), output)
        from PIL import Image
        assert Image.open(result).size == (WIDTH, HEIGHT)

    def test_empty_data_falls_back_to_unavailable(self, tmp_path):
        output = str(tmp_path / "graph.bmp")
        result = render_graph({}, {"hourly": {"time": [], "temperature_2m": []}}, output)
        from PIL import Image
        assert Image.open(result).size == (WIDTH, HEIGHT)

    def test_single_hour_does_not_raise(self, tmp_path):
        """n == 1 hits the ellipse-point branch instead of draw.line, and
        x_at()/mark() must not divide by zero when n <= 1."""
        output = str(tmp_path / "graph.bmp")
        data = {
            "hourly": {
                "time": ["2026-01-01T00:00"],
                "temperature_2m": [50],
                "precipitation_probability": [0],
            }
        }
        result = render_graph({}, data, output)
        assert os.path.exists(result)

    def test_constant_temperature_does_not_raise(self, tmp_path):
        """tmax == tmin must not produce a zero-width y-scale division."""
        output = str(tmp_path / "graph.bmp")
        data = {
            "hourly": {
                "time": ["2026-01-01T00:00", "2026-01-01T01:00"],
                "temperature_2m": [50, 50],
                "precipitation_probability": [0, 0],
            }
        }
        result = render_graph({}, data, output)
        assert os.path.exists(result)

    def test_no_location_name_omits_label_without_raising(self, tmp_path):
        output = str(tmp_path / "graph.bmp")
        result = render_graph({}, _two_hour_data(), output)
        assert os.path.exists(result)


class TestGenerate:
    @patch("modules.forecast_graph.fetch_forecast")
    def test_no_data_renders_unavailable(self, mock_fetch, tmp_path):
        mock_fetch.return_value = None
        output = str(tmp_path / "graph.bmp")
        config = {"forecast_graph": {"output_path": output}}
        result = generate(config)
        assert result == output
        from PIL import Image
        assert Image.open(result).size == (WIDTH, HEIGHT)

    @patch("modules.forecast_graph.fetch_forecast")
    def test_success_renders_graph(self, mock_fetch, tmp_path):
        mock_fetch.return_value = _two_hour_data()
        output = str(tmp_path / "graph.bmp")
        config = {"forecast_graph": {"output_path": output}, "forecast_location": {"name": "Franklin, TN"}}
        result = generate(config)
        assert result == output
        assert os.path.exists(output)

    @patch("modules.forecast_graph.render_graph")
    @patch("modules.forecast_graph.fetch_forecast")
    def test_render_exception_falls_back_to_unavailable(self, mock_fetch, mock_render, tmp_path):
        mock_fetch.return_value = _two_hour_data()
        mock_render.side_effect = RuntimeError("boom")
        output = str(tmp_path / "graph.bmp")
        config = {"forecast_graph": {"output_path": output}}
        result = generate(config)
        assert result == output
        from PIL import Image
        assert Image.open(result).size == (WIDTH, HEIGHT)

    @patch("modules.forecast_graph.fetch_forecast")
    def test_creates_output_parent_directory(self, mock_fetch, tmp_path):
        mock_fetch.return_value = None
        output = str(tmp_path / "nested" / "dir" / "graph.bmp")
        config = {"forecast_graph": {"output_path": output}}
        generate(config)
        assert os.path.exists(output)
