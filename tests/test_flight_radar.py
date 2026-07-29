"""
Unit tests for modules/flight_radar.py: squawk priority, aircraft
selection/sorting, lat/lon-to-pixel projection, silhouette selection,
and TTL-based caching.
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

from modules.flight_radar import (
    _squawk_priority,
    _select_display_aircraft,
    _latlon_to_pixel,
    _pick_silhouette,
    _fetch_aircraft,
    _load_cache,
    _load_stale_cache,
    _save_cache,
    _cache_path,
    SQUAWK_EMERGENCY,
    SQUAWK_HIGH,
)


class TestSquawkPriority:
    def test_emergency_squawk_is_priority_zero(self):
        assert _squawk_priority("7700") == 0

    def test_high_priority_squawks(self):
        assert _squawk_priority("7500") == 1
        assert _squawk_priority("7600") == 1

    def test_normal_squawk_is_priority_two(self):
        assert _squawk_priority("1200") == 2
        assert _squawk_priority("") == 2


class TestSelectDisplayAircraft:
    def _ac(self, icao, lat, lon, squawk=""):
        return {"icao24": icao, "latitude": lat, "longitude": lon, "squawk": squawk}

    def test_selects_closest_three_by_distance(self):
        aircraft = [
            self._ac("a", 40.0, -80.0),
            self._ac("b", 35.9, -86.9),   # closest to center
            self._ac("c", 45.0, -90.0),
            self._ac("d", 36.0, -87.0),   # second closest
        ]
        result = _select_display_aircraft(aircraft, 35.8911, -86.8217)
        assert len(result) == 3
        assert result[0]["icao24"] == "b"

    def test_emergency_squawk_takes_priority_over_distance(self):
        aircraft = [
            self._ac("near", 35.9, -86.9),               # very close, normal
            self._ac("far_emergency", 45.0, -95.0, "7700"),  # far, emergency
        ]
        result = _select_display_aircraft(aircraft, 35.8911, -86.8217)
        assert result[0]["icao24"] == "far_emergency"

    def test_adds_distance_nm_field(self):
        aircraft = [self._ac("a", 36.0, -87.0)]
        result = _select_display_aircraft(aircraft, 35.8911, -86.8217)
        assert "distance_nm" in result[0]
        assert result[0]["distance_nm"] > 0

    def test_returns_at_most_three(self):
        aircraft = [self._ac(str(i), 35.9 + i * 0.01, -86.9) for i in range(10)]
        result = _select_display_aircraft(aircraft, 35.8911, -86.8217)
        assert len(result) == 3

    def test_empty_list_returns_empty(self):
        assert _select_display_aircraft([], 35.8911, -86.8217) == []


class TestLatLonToPixel:
    def test_center_point_maps_to_canvas_center(self):
        px, py = _latlon_to_pixel(35.0, -86.0, 35.0, -86.0, zoom=9, w=600, h=480)
        assert px == 300
        assert py == 240

    def test_point_east_of_center_has_larger_x(self):
        cx, cy = _latlon_to_pixel(35.0, -85.0, 35.0, -86.0, zoom=9, w=600, h=480)
        assert cx > 300

    def test_point_north_of_center_has_smaller_y(self):
        px, py = _latlon_to_pixel(36.0, -86.0, 35.0, -86.0, zoom=9, w=600, h=480)
        assert py < 240


class TestPickSilhouette:
    def test_category_2_is_small_plane_not_helicopter(self):
        points, is_heli = _pick_silhouette(2)
        assert is_heli is False
        assert isinstance(points, list)

    def test_category_8_is_helicopter(self):
        result, is_heli = _pick_silhouette(8)
        assert is_heli is True
        rotor, body = result
        assert isinstance(rotor, list)
        assert isinstance(body, list)

    def test_airliner_categories_4_5_6(self):
        for cat in (4, 5, 6):
            points, is_heli = _pick_silhouette(cat)
            assert is_heli is False
            assert len(points) > 0

    def test_unknown_category_falls_back_to_small_plane(self):
        points, is_heli = _pick_silhouette(999)
        default_points, default_heli = _pick_silhouette(0)
        assert points == default_points
        assert is_heli == default_heli

    def test_non_int_category_falls_back(self):
        points, is_heli = _pick_silhouette(None)
        assert is_heli is False


class TestFetchAircraft:
    def _states_response(self, states):
        return {"states": states}

    @patch("modules.flight_radar.requests.get")
    def test_skips_on_ground_aircraft(self, mock_get):
        # Index 8 = on_ground boolean
        state = ["abc123", "UAL1  ", "US", 0, 0, -86.8, 35.9, 1000, True,
                 200, 90, 0, 0, 0, "1200", False, 0, 3]
        mock_get.return_value = MagicMock(
            json=lambda: self._states_response([state]), raise_for_status=lambda: None
        )
        result = _fetch_aircraft(35.8911, -86.8217, 1.0)
        assert result == []

    @patch("modules.flight_radar.requests.get")
    def test_skips_aircraft_with_no_position(self, mock_get):
        state = ["abc123", "UAL1  ", "US", 0, 0, None, None, 1000, False,
                 200, 90, 0, 0, 0, "1200", False, 0, 3]
        mock_get.return_value = MagicMock(
            json=lambda: self._states_response([state]), raise_for_status=lambda: None
        )
        result = _fetch_aircraft(35.8911, -86.8217, 1.0)
        assert result == []

    @patch("modules.flight_radar.requests.get")
    def test_parses_valid_aircraft(self, mock_get):
        state = ["abc123", "UAL1  ", "US", 0, 0, -86.8, 35.9, 1000, False,
                 200, 90, 0, 0, 0, "1200", False, 0, 3]
        mock_get.return_value = MagicMock(
            json=lambda: self._states_response([state]), raise_for_status=lambda: None
        )
        result = _fetch_aircraft(35.8911, -86.8217, 1.0)
        assert len(result) == 1
        ac = result[0]
        assert ac["icao24"] == "abc123"
        assert ac["callsign"] == "UAL1"
        assert ac["latitude"] == 35.9
        assert ac["longitude"] == -86.8

    @patch("modules.flight_radar.requests.get")
    def test_returns_none_on_request_exception(self, mock_get):
        mock_get.side_effect = Exception("network down")
        result = _fetch_aircraft(35.8911, -86.8217, 1.0)
        assert result is None

    @patch("modules.flight_radar.requests.get")
    def test_handles_missing_states_key(self, mock_get):
        mock_get.return_value = MagicMock(json=lambda: {}, raise_for_status=lambda: None)
        result = _fetch_aircraft(35.8911, -86.8217, 1.0)
        assert result == []


class TestCache:
    def test_save_and_load_within_ttl(self, tmp_path):
        cache_dir = str(tmp_path)
        data = [{"icao24": "abc"}]
        _save_cache(cache_dir, data)
        loaded = _load_cache(cache_dir, ttl_seconds=300)
        assert loaded == data

    def test_load_returns_none_when_missing(self, tmp_path):
        assert _load_cache(str(tmp_path), ttl_seconds=300) is None

    def test_load_returns_none_when_expired(self, tmp_path):
        cache_dir = str(tmp_path)
        _save_cache(cache_dir, [{"icao24": "abc"}])
        path = _cache_path(cache_dir)
        # Backdate the file's mtime beyond the TTL window.
        old_time = time.time() - 1000
        os.utime(path, (old_time, old_time))
        assert _load_cache(cache_dir, ttl_seconds=300) is None

    def test_stale_cache_ignores_ttl(self, tmp_path):
        cache_dir = str(tmp_path)
        _save_cache(cache_dir, [{"icao24": "xyz"}])
        path = _cache_path(cache_dir)
        old_time = time.time() - 100000
        os.utime(path, (old_time, old_time))
        loaded = _load_stale_cache(cache_dir)
        assert loaded == [{"icao24": "xyz"}]

    def test_stale_cache_returns_none_when_missing(self, tmp_path):
        assert _load_stale_cache(str(tmp_path)) is None

    def test_load_cache_handles_corrupt_json(self, tmp_path):
        cache_dir = str(tmp_path)
        path = _cache_path(cache_dir)
        with open(path, "w") as f:
            f.write("{not json")
        assert _load_cache(cache_dir, ttl_seconds=300) is None
