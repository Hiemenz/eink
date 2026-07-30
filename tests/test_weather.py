"""
Unit tests for pure-logic helpers in modules/weather.py (radar module).

Focuses on functions that don't require network access: compass/label
formatting, state persistence, image comparison/quantization, and the
patch cross-correlation storm-motion estimator.
"""

import sys
import os

import numpy as np
import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from unittest.mock import patch, MagicMock

from modules.weather import (
    _deg_to_compass,
    _wmo_description,
    _parse_time,
    distance,
    images_are_equal,
    quantize_to_seven_colors,
    load_state,
    save_state,
    update_top5,
    calculate_non_bw_percentage,
    _compute_storm_motion,
    _do_fetch_conditions,
    _fetch_lightning_strikes,
    _draw_lightning_overlay,
    _draw_status_badges,
)


class TestDegToCompass:
    def test_cardinal_points(self):
        assert _deg_to_compass(0) == "N"
        assert _deg_to_compass(90) == "E"
        assert _deg_to_compass(180) == "S"
        assert _deg_to_compass(270) == "W"

    def test_wraps_at_360(self):
        assert _deg_to_compass(360) == "N"

    def test_intermediate_point(self):
        assert _deg_to_compass(45) == "NE"
        assert _deg_to_compass(135) == "SE"


class TestWmoDescription:
    def test_known_codes(self):
        assert _wmo_description(0) == "Clear"
        assert _wmo_description(61) == "Light Rain"
        assert _wmo_description(95) == "Thunderstorm"

    def test_unknown_code_falls_back(self):
        assert _wmo_description(12345) == "Code 12345"


class TestParseTime:
    def test_valid_iso_string(self):
        result = _parse_time("2026-07-22T06:42:00")
        assert result == "6:42 AM"

    def test_invalid_string_returned_as_is(self):
        assert _parse_time("not-a-timestamp") == "not-a-timestamp"


class TestDistance:
    def test_zero_distance(self):
        assert distance((10, 20, 30), (10, 20, 30)) == 0

    def test_known_distance(self):
        # 3-4-5-ish triangle in RGB space: (0,0,0) -> (0,3,4) = 5
        assert distance((0, 0, 0), (0, 3, 4)) == pytest.approx(5.0)


class TestImagesAreEqual:
    def test_identical_images(self):
        img1 = Image.new("RGB", (10, 10), (255, 0, 0))
        img2 = Image.new("RGB", (10, 10), (255, 0, 0))
        assert images_are_equal(img1, img2) is True

    def test_different_size(self):
        img1 = Image.new("RGB", (10, 10), (255, 0, 0))
        img2 = Image.new("RGB", (20, 20), (255, 0, 0))
        assert images_are_equal(img1, img2) is False

    def test_different_mode(self):
        img1 = Image.new("RGB", (10, 10), (255, 0, 0))
        img2 = Image.new("RGBA", (10, 10), (255, 0, 0, 255))
        assert images_are_equal(img1, img2) is False

    def test_different_pixels(self):
        img1 = Image.new("RGB", (10, 10), (255, 0, 0))
        img2 = Image.new("RGB", (10, 10), (0, 255, 0))
        assert images_are_equal(img1, img2) is False


class TestQuantizeToSevenColors:
    def test_near_white_snaps_to_white(self, tmp_path):
        input_path = tmp_path / "in.png"
        output_path = tmp_path / "out.bmp"
        Image.new("RGB", (4, 4), (250, 250, 250)).save(input_path)

        quantize_to_seven_colors(str(input_path), str(output_path), more_colors=False, threshold=10)

        out = Image.open(output_path).convert("RGB")
        assert out.getpixel((0, 0)) == (255, 255, 255)

    def test_pure_red_maps_to_red(self, tmp_path):
        input_path = tmp_path / "in.png"
        output_path = tmp_path / "out.bmp"
        Image.new("RGB", (4, 4), (255, 0, 0)).save(input_path)

        quantize_to_seven_colors(str(input_path), str(output_path), more_colors=False, threshold=0)

        out = Image.open(output_path).convert("RGB")
        assert out.getpixel((0, 0)) == (255, 0, 0)

    def test_arbitrary_color_maps_to_nearest_palette_entry(self, tmp_path):
        input_path = tmp_path / "in.png"
        output_path = tmp_path / "out.bmp"
        # Closer to orange (255,128,0) than any other 6-color-palette entry.
        Image.new("RGB", (4, 4), (240, 140, 10)).save(input_path)

        quantize_to_seven_colors(str(input_path), str(output_path), more_colors=False, threshold=0)

        out = Image.open(output_path).convert("RGB")
        assert out.getpixel((0, 0)) == (255, 128, 0)


class TestStateRoundTrip:
    def test_missing_file_returns_none(self, tmp_path):
        assert load_state(str(tmp_path / "does_not_exist.json")) is None

    def test_save_then_load_round_trip(self, tmp_path):
        state_file = str(tmp_path / "state.json")
        state = {"frame_ts": 1784740800, "station": "KOHX"}
        save_state(state_file, state)
        assert load_state(state_file) == state


class TestUpdateTop5:
    def test_returns_top_five_sorted_descending(self):
        percentages = {
            "AAA": 10.0, "BBB": 50.0, "CCC": 30.0,
            "DDD": 5.0, "EEE": 40.0, "FFF": 20.0,
        }
        top5 = update_top5(percentages)
        assert len(top5) == 5
        assert [name for name, _ in top5] == ["BBB", "EEE", "CCC", "FFF", "AAA"]

    def test_fewer_than_five_returns_all(self):
        percentages = {"AAA": 1.0, "BBB": 2.0}
        top5 = update_top5(percentages)
        assert len(top5) == 2


class TestCalculateNonBwPercentage:
    def test_all_black_and_white_is_zero_percent(self, tmp_path):
        path = tmp_path / "bw.png"
        img = Image.new("RGB", (2, 2), (0, 0, 0))
        img.putpixel((0, 0), (255, 255, 255))
        img.save(path)
        assert calculate_non_bw_percentage(str(path)) == 0.0

    def test_all_colored_is_hundred_percent(self, tmp_path):
        path = tmp_path / "color.png"
        Image.new("RGB", (2, 2), (0, 255, 0)).save(path)
        assert calculate_non_bw_percentage(str(path)) == pytest.approx(100.0)

    def test_mixed_pixels_partial_percentage(self, tmp_path):
        path = tmp_path / "mixed.png"
        img = Image.new("RGB", (2, 2), (0, 0, 0))
        img.putpixel((0, 0), (0, 255, 0))
        img.save(path)
        assert calculate_non_bw_percentage(str(path)) == pytest.approx(25.0)


def _rgba_with_blob(size, blob_top, blob_left, blob_size, seed=0):
    """Build an RGBA image with a textured (non-uniform) blob, rest transparent.

    A flat/uniform-alpha square is a degenerate case for phase cross-correlation
    (autocorrelation of a constant patch is ambiguous and blows up the FFT
    normalization), so the blob gets reproducible noise texture instead —
    closer to real radar reflectivity data.
    """
    h, w = size
    alpha = np.zeros((h, w), dtype=np.uint8)
    by, bx = blob_top, blob_left
    bs = blob_size
    rng = np.random.RandomState(seed)
    alpha[by:by + bs, bx:bx + bs] = rng.randint(100, 256, size=(bs, bs), dtype=np.uint8)
    rgb = np.zeros((h, w, 3), dtype=np.uint8)
    rgba = np.dstack([rgb, alpha])
    return Image.fromarray(rgba, mode="RGBA")


class TestComputeStormMotion:
    def test_returns_none_for_empty_images(self):
        prev = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
        curr = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
        assert _compute_storm_motion(prev, curr) is None

    def test_returns_none_for_stationary_blob(self):
        prev = _rgba_with_blob((200, 200), 70, 70, 60)
        curr = _rgba_with_blob((200, 200), 70, 70, 60)
        # Zero displacement -> magnitude < 1.0 threshold -> None.
        assert _compute_storm_motion(prev, curr) is None

    def test_recovers_shift_direction_and_magnitude_not_negated(self):
        # Storm moves 10px east (+x) and 6px north (-y, since image y grows downward).
        dx, dy = 10, -6
        prev = _rgba_with_blob((200, 200), 70, 70, 60)
        curr = _rgba_with_blob((200, 200), 70 + dy, 70 + dx, 60)

        result = _compute_storm_motion(prev, curr)

        assert result is not None
        vx, vy = result
        # Must match the forward (prev -> curr) displacement directly, per the
        # documented convention: negating this would point arrows backwards.
        assert vx == pytest.approx(dx, abs=3)
        assert vy == pytest.approx(dy, abs=3)


def _open_meteo_response(weather_code: int, freezing_level_m: float, elevation_m: float = 180.0) -> dict:
    """Minimal Open-Meteo response fixture shaped for _do_fetch_conditions()."""
    return {
        "timezone": "America/Chicago",
        "elevation": elevation_m,
        "current": {
            "time": "2026-01-15T12:00",
            "temperature_2m": 28.0,
            "apparent_temperature": 19.0,
            "relative_humidity_2m": 88,
            "weather_code": weather_code,
            "surface_pressure": 1013.0,
            "wind_speed_10m": 12.0,
            "wind_direction_10m": 300,
            "wind_gusts_10m": 20.0,
            "uv_index": 0,
            "is_day": 1,
        },
        "hourly": {
            "time": ["2026-01-15T12:00"],
            "visibility": [8000],
            "surface_pressure": [1013.0],
            "relative_humidity_2m": [88],
            "temperature_2m": [28.0],
            "weather_code": [weather_code],
            "precipitation_probability": [80],
            "uv_index": [0],
            "freezing_level_height": [freezing_level_m],
        },
        "daily": {
            "sunrise": ["2026-01-15T07:10"],
            "sunset": ["2026-01-15T17:40"],
            "precipitation_sum": [0.4],
            "temperature_2m_max": [30.0],
            "temperature_2m_min": [22.0],
        },
    }


class TestPrecipTypeClassification:
    """_do_fetch_conditions() estimates rain/snow/mix from freezing-level height
    above ground during active precip — radar reflectivity alone can't tell these
    apart, so this fills the gap. See docstring in modules/weather.py."""

    @patch("modules.weather.requests.get")
    def test_low_freezing_level_during_snow_code_classifies_snow(self, mock_get):
        # Snow code (71) + freezing level 100m above a 180m-elevation site -> AGL well under 300m.
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = _open_meteo_response(weather_code=71, freezing_level_m=280.0, elevation_m=180.0)
        mock_get.return_value = resp

        result = _do_fetch_conditions("http://fake", 35.9, -86.8, {})
        assert result["precip_type"] == "Snow"

    @patch("modules.weather.requests.get")
    def test_mid_freezing_level_classifies_wintry_mix(self, mock_get):
        # AGL = 900 - 180 = 720m -> between 300 and 1000 -> Wintry Mix.
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = _open_meteo_response(weather_code=61, freezing_level_m=900.0, elevation_m=180.0)
        mock_get.return_value = resp

        result = _do_fetch_conditions("http://fake", 35.9, -86.8, {})
        assert result["precip_type"] == "Wintry Mix"

    @patch("modules.weather.requests.get")
    def test_high_freezing_level_during_rain_has_no_precip_type(self, mock_get):
        # AGL well over 1000m during active rain -> plain rain, no badge needed.
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = _open_meteo_response(weather_code=61, freezing_level_m=2000.0, elevation_m=180.0)
        mock_get.return_value = resp

        result = _do_fetch_conditions("http://fake", 35.9, -86.8, {})
        assert result["precip_type"] is None

    @patch("modules.weather.requests.get")
    def test_low_freezing_level_without_active_precip_has_no_precip_type(self, mock_get):
        # Clear-sky code (0) with a low freezing level -> not raining/snowing, no badge.
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = _open_meteo_response(weather_code=0, freezing_level_m=100.0, elevation_m=180.0)
        mock_get.return_value = resp

        result = _do_fetch_conditions("http://fake", 35.9, -86.8, {})
        assert result["precip_type"] is None


class TestFetchLightningStrikes:
    def test_missing_credentials_returns_empty_without_network_call(self):
        with patch("modules.weather.requests.get") as mock_get:
            result = _fetch_lightning_strikes(35.9, -86.8, {}, "", "")
        assert result == []
        mock_get.assert_not_called()

    @patch("modules.weather.requests.get")
    def test_parses_strikes_from_response_envelope(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {
            "response": [
                {"loc": {"lat": 36.0, "long": -86.9}},
                {"loc": {"lat": 35.8, "long": -86.7}},
            ]
        }
        mock_get.return_value = resp

        result = _fetch_lightning_strikes(35.9, -86.8, {}, "id", "secret")
        assert result == [{"lat": 36.0, "lon": -86.9}, {"lat": 35.8, "lon": -86.7}]

    @patch("modules.weather.requests.get")
    def test_request_exception_returns_empty(self, mock_get):
        import requests
        mock_get.side_effect = requests.exceptions.RequestException("boom")
        assert _fetch_lightning_strikes(35.9, -86.8, {}, "id", "secret") == []

    @patch("modules.weather.requests.get")
    def test_strike_missing_loc_fields_is_skipped(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"response": [{"loc": {}}, {"loc": {"lat": 36.0, "long": -86.9}}]}
        mock_get.return_value = resp

        result = _fetch_lightning_strikes(35.9, -86.8, {}, "id", "secret")
        assert result == [{"lat": 36.0, "lon": -86.9}]


class TestDrawLightningOverlay:
    def test_no_strikes_leaves_canvas_unchanged(self):
        canvas = Image.new("RGB", (100, 100), (255, 255, 255))
        _draw_lightning_overlay(canvas, [], 35.9, -86.8, 7, 100, 100)
        assert list(canvas.getdata()) == [(255, 255, 255)] * (100 * 100)

    def test_strike_near_center_draws_black_core(self):
        canvas = Image.new("RGB", (200, 200), (255, 255, 255))
        strikes = [{"lat": 35.9, "lon": -86.8}]
        _draw_lightning_overlay(canvas, strikes, 35.9, -86.8, 7, 200, 200)
        # Center pixel should now be part of the black core marker.
        assert canvas.getpixel((100, 100)) == (0, 0, 0)

    def test_strike_outside_region_is_not_drawn(self):
        canvas = Image.new("RGB", (100, 100), (255, 255, 255))
        # Far outside the visible region at zoom 7 -> projects well off-canvas.
        strikes = [{"lat": 45.0, "lon": -70.0}]
        _draw_lightning_overlay(canvas, strikes, 35.9, -86.8, 7, 100, 100)
        assert list(canvas.getdata()) == [(255, 255, 255)] * (100 * 100)


_BASE_BADGE_CONFIG = {
    "river_height": {"site_number": "03432350", "cache_dir": "data/"},
    "air_quality": {"zip_code": "37064", "api_key": "", "cache_dir": "data/"},
}


class TestDrawStatusBadges:
    def test_no_site_number_and_no_api_key_draws_nothing(self):
        canvas = Image.new("RGB", (280, 200), (255, 255, 255))
        config = {"river_height": {"site_number": ""}, "air_quality": {"api_key": ""}}
        y = _draw_status_badges(canvas, config, 0, 280, 20)
        assert y == 20
        assert list(canvas.getdata()) == [(255, 255, 255)] * (280 * 200)

    @patch("modules.weather._get_river_data")
    @patch("modules.weather._resolve_thresholds")
    def test_river_above_action_stage_draws_badge_in_stage_color(self, mock_thresholds, mock_river):
        mock_river.return_value = {"current_ft": 9.5, "history": [], "fetched_at": 0}
        mock_thresholds.return_value = {
            "action_stage": 8.0, "flood_stage": 10.0,
            "moderate_flood_stage": 12.0, "major_flood_stage": 15.0,
        }
        canvas = Image.new("RGB", (280, 200), (255, 255, 255))
        y = _draw_status_badges(canvas, _BASE_BADGE_CONFIG, 0, 280, 20)
        assert y == 20 + 20 + 3  # start_y + _BADGE_H + gap
        # Action Stage color (255, 220, 0) should now appear in the drawn strip.
        assert (255, 220, 0) in set(canvas.crop((0, 20, 280, 40)).getdata())

    @patch("modules.weather._get_river_data")
    def test_river_at_normal_stage_draws_nothing(self, mock_river):
        mock_river.return_value = {"current_ft": 2.0, "history": [], "fetched_at": 0}
        # Real config.yml thresholds are all 0.0 (unconfigured) -> always "Normal".
        canvas = Image.new("RGB", (280, 200), (255, 255, 255))
        y = _draw_status_badges(canvas, _BASE_BADGE_CONFIG, 0, 280, 20)
        assert y == 20
        assert list(canvas.getdata()) == [(255, 255, 255)] * (280 * 200)

    @patch("modules.weather._get_aqi_data")
    def test_aqi_at_or_below_moderate_threshold_draws_nothing(self, mock_aqi):
        mock_aqi.return_value = {"aqi": 50, "category": "Moderate", "fetched_at": 0}
        config = {**_BASE_BADGE_CONFIG,
                  "air_quality": {**_BASE_BADGE_CONFIG["air_quality"], "api_key": "fake"}}
        canvas = Image.new("RGB", (280, 200), (255, 255, 255))
        y = _draw_status_badges(canvas, config, 0, 280, 20)
        assert y == 20

    @patch("modules.weather._get_river_data")
    @patch("modules.weather._resolve_thresholds")
    @patch("modules.weather._get_aqi_data")
    def test_river_and_aqi_both_trigger_merge_into_one_badge(self, mock_aqi, mock_thresholds, mock_river):
        mock_river.return_value = {"current_ft": 12.3, "history": [], "fetched_at": 0}
        mock_thresholds.return_value = {
            "action_stage": 8.0, "flood_stage": 10.0,
            "moderate_flood_stage": 12.0, "major_flood_stage": 15.0,
        }
        mock_aqi.return_value = {"aqi": 168, "category": "Unhealthy", "fetched_at": 0}
        config = {**_BASE_BADGE_CONFIG,
                  "air_quality": {**_BASE_BADGE_CONFIG["air_quality"], "api_key": "fake"}}
        canvas = Image.new("RGB", (280, 200), (255, 255, 255))
        y = _draw_status_badges(canvas, config, 0, 280, 20)
        # Exactly one badge row, not two stacked -- merged onto a single line.
        assert y == 20 + 20 + 3

    def test_insufficient_headroom_skips_badge_entirely(self):
        # start_y within 110px of the bottom -> guard returns start_y unchanged,
        # protecting the always-present hourly forecast strip beneath it.
        canvas = Image.new("RGB", (280, 480), (255, 255, 255))
        with patch("modules.weather._get_river_data") as mock_river, \
             patch("modules.weather._resolve_thresholds") as mock_thresholds:
            mock_river.return_value = {"current_ft": 9.5, "history": [], "fetched_at": 0}
            mock_thresholds.return_value = {"action_stage": 8.0, "flood_stage": 10.0,
                                             "moderate_flood_stage": 12.0, "major_flood_stage": 15.0}
            y = _draw_status_badges(canvas, _BASE_BADGE_CONFIG, 0, 280, 400)
        assert y == 400
        mock_river.assert_not_called()  # guard short-circuits before any data fetch
