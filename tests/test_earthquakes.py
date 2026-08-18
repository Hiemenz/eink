"""
Unit tests for modules/earthquakes.py — magnitude color/radius mapping,
haversine distance, "time ago" formatting, cache TTL, and USGS GeoJSON
parsing (network calls mocked).
"""

import json
import math
import os
import sys
import time
from unittest.mock import patch, MagicMock

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules import earthquakes as eq


class TestMagColor:
    def test_minor(self):
        assert eq._mag_color(2.0) == eq.GREEN

    def test_light(self):
        assert eq._mag_color(4.0) == eq.YELLOW

    def test_moderate(self):
        assert eq._mag_color(5.5) == eq.ORANGE

    def test_major(self):
        assert eq._mag_color(6.5) == eq.RED

    def test_boundary_3(self):
        assert eq._mag_color(3.0) == eq.YELLOW

    def test_boundary_6(self):
        assert eq._mag_color(6.0) == eq.RED


class TestMagRadius:
    def test_zero_magnitude(self):
        assert eq._mag_radius(0.0) == 2

    def test_scales_with_magnitude(self):
        assert eq._mag_radius(4.0) == 8

    def test_negative_magnitude_clamped(self):
        assert eq._mag_radius(-1.0) == 2


class TestHaversineKm:
    def test_same_point_is_zero(self):
        assert eq._haversine_km(35.0, -86.0, 35.0, -86.0) == pytest.approx(0.0, abs=1e-6)

    def test_known_distance_nyc_to_la(self):
        # Approx great-circle distance NYC -> LA is ~3936 km
        d = eq._haversine_km(40.7128, -74.0060, 34.0522, -118.2437)
        assert d == pytest.approx(3936, rel=0.02)

    def test_antipodal_points(self):
        d = eq._haversine_km(0, 0, 0, 180)
        assert d == pytest.approx(math.pi * 6371.0088, rel=0.01)


class TestAgo:
    def test_minutes_ago(self):
        now = 1000000.0
        event_ms = (now - 300) * 1000  # 5 minutes ago
        assert eq._ago(event_ms, now) == "5m ago"

    def test_hours_ago(self):
        now = 1000000.0
        event_ms = (now - 7200) * 1000  # 2 hours ago
        assert eq._ago(event_ms, now) == "2h ago"

    def test_days_ago(self):
        now = 1000000.0
        event_ms = (now - 3 * 86400) * 1000
        assert eq._ago(event_ms, now) == "3d ago"

    def test_future_event_clamped_to_zero(self):
        now = 1000000.0
        event_ms = (now + 1000) * 1000  # in the future
        assert eq._ago(event_ms, now) == "0m ago"


class TestCache:
    def test_load_missing(self, tmp_path):
        assert eq._load_cache(str(tmp_path)) is None

    def test_save_and_load(self, tmp_path):
        cache_dir = str(tmp_path)
        eq._save_cache(cache_dir, {"feed": "2.5_day", "quakes": [], "fetched_at": time.time()})
        data = eq._load_cache(cache_dir)
        assert data["feed"] == "2.5_day"

    def test_load_corrupt(self, tmp_path):
        path = tmp_path / "earthquakes_cache.json"
        path.write_text("{not json")
        assert eq._load_cache(str(tmp_path)) is None


class TestFetchQuakes:
    def test_success_normalizes_features(self):
        geojson = {
            "features": [
                {
                    "properties": {"mag": 4.5, "place": "10km N of Somewhere", "time": 1234567890},
                    "geometry": {"coordinates": [-86.8, 35.9, 10.0]},
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = geojson
        with patch.object(eq.requests, "get", return_value=mock_resp):
            quakes = eq._fetch_quakes("2.5_day")
        assert len(quakes) == 1
        assert quakes[0]["mag"] == 4.5
        assert quakes[0]["lon"] == -86.8
        assert quakes[0]["lat"] == 35.9
        assert quakes[0]["depth"] == 10.0

    def test_network_failure_returns_none(self):
        with patch.object(eq.requests, "get", side_effect=Exception("timeout")):
            assert eq._fetch_quakes("2.5_day") is None

    def test_missing_features_key_returns_none(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"unexpected": "shape"}
        with patch.object(eq.requests, "get", return_value=mock_resp):
            assert eq._fetch_quakes("2.5_day") is None

    def test_skips_feature_missing_magnitude(self):
        geojson = {
            "features": [
                {"properties": {"place": "no mag"}, "geometry": {"coordinates": [-86.8, 35.9]}},
                {
                    "properties": {"mag": 3.0, "place": "has mag", "time": 1},
                    "geometry": {"coordinates": [-86.8, 35.9]},
                },
            ]
        }
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = geojson
        with patch.object(eq.requests, "get", return_value=mock_resp):
            quakes = eq._fetch_quakes("2.5_day")
        assert len(quakes) == 1
        assert quakes[0]["place"] == "has mag"

    def test_missing_place_defaults_to_unknown(self):
        geojson = {
            "features": [
                {"properties": {"mag": 3.0, "time": 1}, "geometry": {"coordinates": [-86.8, 35.9]}},
            ]
        }
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = geojson
        with patch.object(eq.requests, "get", return_value=mock_resp):
            quakes = eq._fetch_quakes("2.5_day")
        assert quakes[0]["place"] == "Unknown location"

    def test_empty_features_returns_empty_list(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"features": []}
        with patch.object(eq.requests, "get", return_value=mock_resp):
            quakes = eq._fetch_quakes("2.5_day")
        assert quakes == []


class TestGetData:
    def test_fresh_cache_same_feed_skips_fetch(self, tmp_path):
        cache_dir = str(tmp_path)
        eq._save_cache(cache_dir, {"feed": "2.5_day", "quakes": [], "fetched_at": time.time()})
        with patch.object(eq, "_fetch_quakes") as mock_fetch:
            data = eq._get_data(cache_dir, "2.5_day")
        mock_fetch.assert_not_called()
        assert data["feed"] == "2.5_day"

    def test_different_feed_forces_refetch(self, tmp_path):
        cache_dir = str(tmp_path)
        eq._save_cache(cache_dir, {"feed": "2.5_day", "quakes": [], "fetched_at": time.time()})
        with patch.object(eq, "_fetch_quakes", return_value=[{"mag": 5.0}]):
            data = eq._get_data(cache_dir, "4.5_day")
        assert data["feed"] == "4.5_day"
        assert data["quakes"] == [{"mag": 5.0}]

    def test_fetch_failure_falls_back_to_stale_cache(self, tmp_path):
        cache_dir = str(tmp_path)
        eq._save_cache(cache_dir, {"feed": "2.5_day", "quakes": [{"mag": 1.0}], "fetched_at": 0})
        with patch.object(eq, "_fetch_quakes", return_value=None):
            data = eq._get_data(cache_dir, "2.5_day")
        assert data["quakes"] == [{"mag": 1.0}]

    def test_no_cache_fetch_fails_returns_none(self, tmp_path):
        cache_dir = str(tmp_path)
        with patch.object(eq, "_fetch_quakes", return_value=None):
            data = eq._get_data(cache_dir, "2.5_day")
        assert data is None


class TestLonLatToXy:
    def test_center_of_map(self):
        x, y = eq._lonlat_to_xy(0, 0)
        assert x == pytest.approx(eq.MAP_X + eq.MAP_W / 2)
        assert y == pytest.approx(eq.MAP_Y + eq.MAP_H / 2)

    def test_top_left_corner(self):
        x, y = eq._lonlat_to_xy(-180, 90)
        assert x == pytest.approx(eq.MAP_X)
        assert y == pytest.approx(eq.MAP_Y)


class TestGenerate:
    """generate() itself has no direct coverage above — only its building blocks do."""

    def _config(self, tmp_path, feed="2.5_day", with_location=True):
        cfg = {
            "earthquakes": {
                "output_path": str(tmp_path / "out.bmp"),
                "cache_dir": str(tmp_path),
                "feed": feed,
            }
        }
        if with_location:
            cfg["forecast_location"] = {"latitude": 35.9251, "longitude": -86.8689, "name": "Franklin, TN"}
        return cfg

    def test_no_data_renders_unavailable_and_writes_file(self, tmp_path):
        config = self._config(tmp_path)
        with patch.object(eq, "_get_data", return_value=None):
            result = eq.generate(config)
        assert result == config["earthquakes"]["output_path"]
        assert os.path.exists(result)

    def test_with_quakes_writes_file(self, tmp_path):
        config = self._config(tmp_path)
        data = {
            "feed": "2.5_day",
            "fetched_at": time.time(),
            "quakes": [
                {"mag": 5.5, "place": "Somewhere", "time": time.time() * 1000, "lon": -86.8, "lat": 35.9, "depth": 5.0},
            ],
        }
        with patch.object(eq, "_get_data", return_value=data):
            result = eq.generate(config)
        assert os.path.exists(result)
        img = Image.open(result)
        assert img.size == (eq.WIDTH, eq.HEIGHT)

    def test_empty_quakes_list_still_renders_frame(self, tmp_path):
        """A successful fetch with zero quakes is not the same as a failed fetch."""
        config = self._config(tmp_path)
        data = {"feed": "2.5_day", "fetched_at": time.time(), "quakes": []}
        with patch.object(eq, "_get_data", return_value=data):
            result = eq.generate(config)
        assert os.path.exists(result)

    def test_missing_forecast_location_skips_observer_marker(self, tmp_path):
        config = self._config(tmp_path, with_location=False)
        data = {
            "feed": "2.5_day",
            "fetched_at": time.time(),
            "quakes": [{"mag": 3.0, "place": "X", "time": time.time() * 1000, "lon": 0, "lat": 0, "depth": 0}],
        }
        with patch.object(eq, "_get_data", return_value=data):
            result = eq.generate(config)
        assert os.path.exists(result)

    def test_creates_output_directory_if_missing(self, tmp_path):
        config = self._config(tmp_path)
        config["earthquakes"]["output_path"] = str(tmp_path / "nested" / "dir" / "out.bmp")
        with patch.object(eq, "_get_data", return_value=None):
            result = eq.generate(config)
        assert os.path.exists(result)

    def test_passes_configured_feed_through_to_get_data(self, tmp_path):
        config = self._config(tmp_path, feed="significant_week")
        with patch.object(eq, "_get_data", return_value=None) as mock_get:
            eq.generate(config)
        mock_get.assert_called_once_with(str(tmp_path), "significant_week")
