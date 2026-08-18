"""
Unit tests for modules/iss_tracker.py: great-circle geometry helpers and
the cache/fetch fallback logic.
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

from modules.iss_tracker import (
    _haversine_km,
    _bearing_deg,
    _compass_point,
    _lonlat_to_xy,
    _get_data,
    _cache_path,
    _fetch_iss,
    generate,
    EARTH_RADIUS_KM,
)


class TestHaversine:
    def test_same_point_is_zero_distance(self):
        assert _haversine_km(35.0, -86.0, 35.0, -86.0) == pytest.approx(0.0, abs=1e-9)

    def test_antipodal_points_are_half_circumference(self):
        dist = _haversine_km(0.0, 0.0, 0.0, 180.0)
        expected = math_pi_earth_half_circumference()
        assert dist == pytest.approx(expected, rel=1e-6)

    def test_known_distance_ny_to_london(self):
        # NYC (40.7128, -74.0060) to London (51.5074, -0.1278) ~ 5570 km
        dist = _haversine_km(40.7128, -74.0060, 51.5074, -0.1278)
        assert dist == pytest.approx(5570, rel=0.02)

    def test_distance_is_symmetric(self):
        d1 = _haversine_km(35.0, -86.0, 40.0, -90.0)
        d2 = _haversine_km(40.0, -90.0, 35.0, -86.0)
        assert d1 == pytest.approx(d2, rel=1e-9)


def math_pi_earth_half_circumference():
    import math
    return math.pi * EARTH_RADIUS_KM


class TestBearing:
    def test_due_north_is_zero(self):
        bearing = _bearing_deg(0.0, 0.0, 1.0, 0.0)
        assert bearing == pytest.approx(0.0, abs=0.5)

    def test_due_east_is_ninety(self):
        bearing = _bearing_deg(0.0, 0.0, 0.0, 1.0)
        assert bearing == pytest.approx(90.0, abs=0.5)

    def test_due_south_is_180(self):
        bearing = _bearing_deg(1.0, 0.0, 0.0, 0.0)
        assert bearing == pytest.approx(180.0, abs=0.5)

    def test_due_west_is_270(self):
        bearing = _bearing_deg(0.0, 0.0, 0.0, -1.0)
        assert bearing == pytest.approx(270.0, abs=0.5)

    def test_bearing_always_in_0_360(self):
        bearing = _bearing_deg(35.0, -86.0, -10.0, 150.0)
        assert 0.0 <= bearing < 360.0


class TestCompassPoint:
    @pytest.mark.parametrize("bearing,expected", [
        (0, "N"), (20, "N"), (46, "NE"), (90, "E"), (135, "SE"),
        (180, "S"), (225, "SW"), (270, "W"), (315, "NW"), (359, "N"),
    ])
    def test_compass_point_buckets(self, bearing, expected):
        assert _compass_point(bearing) == expected


class TestLonLatToXY:
    def test_origin_maps_to_map_center(self):
        x, y = _lonlat_to_xy(0.0, 0.0, x0=0, y0=0, w=360, h=180)
        assert x == 180
        assert y == 90

    def test_west_edge(self):
        x, y = _lonlat_to_xy(-180.0, 0.0, x0=0, y0=0, w=360, h=180)
        assert x == 0

    def test_north_pole_maps_to_top(self):
        x, y = _lonlat_to_xy(0.0, 90.0, x0=0, y0=0, w=360, h=180)
        assert y == 0

    def test_south_pole_maps_to_bottom(self):
        x, y = _lonlat_to_xy(0.0, -90.0, x0=0, y0=0, w=360, h=180)
        assert y == 180

    def test_offset_origin_applied(self):
        x, y = _lonlat_to_xy(0.0, 0.0, x0=10, y0=20, w=360, h=180)
        assert x == 190
        assert y == 110


class TestGetData:
    def test_uses_fresh_cache_without_network_call(self, tmp_path):
        cache_dir = str(tmp_path)
        cache_file = _cache_path(cache_dir)
        payload = {"latitude": 1.0, "longitude": 2.0, "fetched_at": time.time()}
        with open(cache_file, "w") as f:
            json.dump(payload, f)

        with patch("modules.iss_tracker.requests.get") as mock_get:
            result = _get_data(cache_dir)
            mock_get.assert_not_called()
        assert result == payload

    @patch("modules.iss_tracker.requests.get")
    def test_fetches_fresh_when_cache_expired(self, mock_get, tmp_path):
        cache_dir = str(tmp_path)
        cache_file = _cache_path(cache_dir)
        stale = {"latitude": 1.0, "longitude": 2.0, "fetched_at": time.time() - 1000}
        with open(cache_file, "w") as f:
            json.dump(stale, f)

        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "latitude": 10.0, "longitude": 20.0, "altitude": 400.0,
            "velocity": 27000.0, "visibility": "daylight", "timestamp": 123456,
        }
        mock_get.return_value = mock_resp

        result = _get_data(cache_dir)
        assert result["latitude"] == 10.0
        assert result["longitude"] == 20.0

    @patch("modules.iss_tracker.requests.get")
    def test_falls_back_to_stale_cache_on_failure(self, mock_get, tmp_path):
        cache_dir = str(tmp_path)
        cache_file = _cache_path(cache_dir)
        stale = {"latitude": 1.0, "longitude": 2.0, "fetched_at": time.time() - 1000}
        with open(cache_file, "w") as f:
            json.dump(stale, f)

        mock_get.side_effect = Exception("network down")

        result = _get_data(cache_dir)
        assert result["latitude"] == 1.0
        assert result["stale"] is True

    @patch("modules.iss_tracker.requests.get")
    def test_returns_none_when_no_cache_and_fetch_fails(self, mock_get, tmp_path):
        mock_get.side_effect = Exception("network down")
        result = _get_data(str(tmp_path))
        assert result is None


class TestFetchIss:
    @patch("modules.iss_tracker.requests.get")
    def test_success_returns_normalized_dict(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {
            "latitude": "10.5", "longitude": "-20.25", "altitude": "408.1",
            "velocity": "27600.2", "visibility": "eclipsed", "timestamp": 111,
        }
        mock_get.return_value = resp
        result = _fetch_iss()
        assert result["latitude"] == 10.5
        assert result["longitude"] == -20.25
        assert result["visibility"] == "eclipsed"
        assert result["timestamp"] == 111
        assert "fetched_at" in result

    @patch("modules.iss_tracker.requests.get")
    def test_missing_visibility_defaults_to_empty_string(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {
            "latitude": "1.0", "longitude": "2.0", "altitude": "400.0", "velocity": "27000.0",
        }
        mock_get.return_value = resp
        result = _fetch_iss()
        assert result["visibility"] == ""

    @patch("modules.iss_tracker.requests.get")
    def test_missing_required_key_raises(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"latitude": "1.0"}  # missing longitude/altitude/velocity
        mock_get.return_value = resp
        with pytest.raises(KeyError):
            _fetch_iss()

    @patch("modules.iss_tracker.requests.get")
    def test_http_error_propagates(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception("500")
        mock_get.return_value = resp
        with pytest.raises(Exception):
            _fetch_iss()


class TestGenerate:
    def _config(self, tmp_path):
        return {
            "iss_tracker": {
                "output_path": str(tmp_path / "out.bmp"),
                "cache_dir": str(tmp_path / "cache"),
            },
            "forecast_location": {"latitude": 35.9251, "longitude": -86.8689},
        }

    @patch("modules.iss_tracker.requests.get")
    def test_unavailable_still_produces_output_file(self, mock_get, tmp_path):
        mock_get.side_effect = Exception("network down")
        config = self._config(tmp_path)
        result = generate(config)
        assert result == config["iss_tracker"]["output_path"]
        assert os.path.exists(result)

    @patch("modules.iss_tracker.requests.get")
    def test_successful_fetch_produces_full_size_image(self, mock_get, tmp_path):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {
            "latitude": 10.0, "longitude": 20.0, "altitude": 408.0,
            "velocity": 27600.0, "visibility": "daylight", "timestamp": 123456,
        }
        mock_get.return_value = resp
        config = self._config(tmp_path)

        result = generate(config)

        assert os.path.exists(result)
        from PIL import Image
        from modules.iss_tracker import WIDTH, HEIGHT
        img = Image.open(result)
        assert img.size == (WIDTH, HEIGHT)

    @patch("modules.iss_tracker.requests.get")
    def test_default_observer_location_when_unconfigured(self, mock_get, tmp_path):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {
            "latitude": 0.0, "longitude": 0.0, "altitude": 400.0,
            "velocity": 27000.0, "visibility": "", "timestamp": 1,
        }
        mock_get.return_value = resp
        config = {"iss_tracker": {
            "output_path": str(tmp_path / "out.bmp"),
            "cache_dir": str(tmp_path / "cache"),
        }}
        result = generate(config)
        assert os.path.exists(result)
