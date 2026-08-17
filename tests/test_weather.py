"""
Unit tests for pure-logic helpers in modules/weather.py (radar module).

Focuses on functions that don't require network access: compass/label
formatting, state persistence, image comparison/quantization, and the
patch cross-correlation storm-motion estimator.
"""

import sys
import os
import time

import numpy as np
import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from unittest.mock import patch, MagicMock

import io
import math

import modules.weather as W_MOD
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
    _patch_motion_vectors,
    _cluster_motion_vectors,
    _bearing_from_vector,
    _storm_speed_kmh,
    _precip_arrival_minutes,
    _nowcast_arrival_minutes,
    _draw_nowcast_outline,
    _draw_corner_labels,
    _remap_radar_seven_color,
    _overlay_severe_alerts,
    _fetch_nws_alerts,
    _redact,
    _first_set,
    _view_bounds,
    _STALE_RADAR_MIN,
    _STALE_COLOR,
    _draw_seven_color_legend,
    _LEGEND_H,
    _draw_stale_banner,
    _STALE_BANNER_H,
    _ALERT_SEVERITY_STYLE,
    _ALERT_DEFAULT_STYLE,
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


# ---------------------------------------------------------------------------
# Radar audit fixes (bugs) and new features
# ---------------------------------------------------------------------------

class TestQuantizePalette:
    """quantize_to_seven_colors must be exact-palette-out."""

    def test_mid_grey_never_survives_as_grey(self, tmp_path):
        # The panel separator used to be drawn in (180,180,180): too far from
        # white to hit the threshold, and white is not a palette entry, so it
        # landed on the nearest ink -- orange -- putting a coloured line down
        # the middle of the display.
        src = tmp_path / "grey.bmp"
        Image.new("RGB", (4, 4), (180, 180, 180)).save(src)
        out = tmp_path / "grey_q.bmp"
        quantize_to_seven_colors(str(src), str(out), more_colors=False, threshold=75)
        colors = {px for px in Image.open(out).convert("RGB").getdata()}
        assert colors == {(255, 128, 0)}, "grey lands on orange -- draw UI in pure B/W"

    def test_pure_black_and_white_are_stable(self, tmp_path):
        src = tmp_path / "bw.bmp"
        img = Image.new("RGB", (2, 1), (0, 0, 0))
        img.putpixel((1, 0), (255, 255, 255))
        img.save(src)
        out = tmp_path / "bw_q.bmp"
        quantize_to_seven_colors(str(src), str(out), more_colors=False, threshold=75)
        result = Image.open(out).convert("RGB")
        assert result.getpixel((0, 0)) == (0, 0, 0)
        assert result.getpixel((1, 0)) == (255, 255, 255)


class TestRemapSevenColorBoundaries:
    """Hue band edges must classify the boundary colour into the band it names."""

    @staticmethod
    def _classify(rgb):
        img = Image.new("RGB", (1, 1), rgb)
        return _remap_radar_seven_color(img).getpixel((0, 0))

    def test_pure_yellow_is_yellow_not_orange(self):
        # Hue of (255,255,0) is exactly 60deg = 0.16666..., which is below a
        # rounded 0.167 literal -- it used to fall through into the orange
        # (heavy rain) tier, over-reading moderate rain by a full dBZ tier.
        assert self._classify((255, 255, 0)) == (255, 255, 0)

    def test_band_centres_land_in_their_own_tier(self):
        assert self._classify((255, 0, 0)) == (255, 0, 0)        # red     0deg
        assert self._classify((255, 128, 0)) == (255, 128, 0)    # orange 30deg
        assert self._classify((0, 255, 0)) == (0, 255, 0)        # green 120deg
        assert self._classify((0, 0, 255)) == (0, 0, 255)        # blue  240deg

    def test_white_background_stays_white(self):
        assert self._classify((255, 255, 255)) == (255, 255, 255)


class TestPatchMotionPeakGate:
    """The weak-peak gate must actually reject uncorrelated patches."""

    def test_noise_pairs_yield_no_confident_vectors(self):
        # Two independently random frames share no real structure. The old gate
        # (peak < mean*3) passed 100% of such pairs because a phase-correlation
        # surface has a mean of essentially zero, feeding junk into the median.
        rng = np.random.RandomState(7)
        def noise():
            a = (rng.rand(200, 200) < 0.05).astype(np.uint8) * 255
            rgb = np.zeros((200, 200, 3), dtype=np.uint8)
            return Image.fromarray(np.dstack([rgb, a]), mode="RGBA")
        assert _compute_storm_motion(noise(), noise()) is None

    def test_real_shift_still_recovered(self):
        prev = _rgba_with_blob((200, 200), 70, 70, 60)
        curr = _rgba_with_blob((200, 200), 64, 80, 60)
        vectors = _patch_motion_vectors(prev, curr)
        assert vectors, "a genuine translation must still produce vectors"
        for cx, cy, dx, dy in vectors:
            assert 0 <= cx <= 200 and 0 <= cy <= 200


class TestClusterMotionVectors:
    """Per-cell arrows: cells that move differently must not merge."""

    def test_adjacent_cells_with_opposite_motion_stay_separate(self):
        # Two touching groups -- close enough to chain on distance alone, but
        # travelling in opposite directions. This is the splitting-supercell
        # case that a single averaged arrow gets wrong for both limbs.
        east = [(100 + i * 20, 100, 10.0, 0.0) for i in range(4)]
        west = [(180 + i * 20, 100, -10.0, 0.0) for i in range(4)]
        clusters = _cluster_motion_vectors(east + west)
        assert len(clusters) == 2
        headings = sorted(round(dx) for _, _, dx, _ in clusters)
        assert headings == [-10, 10]

    def test_coherent_group_collapses_to_one_arrow(self):
        vectors = [(100 + i * 20, 100, 8.0, -3.0) for i in range(5)]
        clusters = _cluster_motion_vectors(vectors)
        assert len(clusters) == 1
        cx, cy, dx, dy = clusters[0]
        assert dx == pytest.approx(8.0)
        assert dy == pytest.approx(-3.0)

    def test_stationary_and_undersized_clusters_dropped(self):
        assert _cluster_motion_vectors([]) == []
        # below min_members
        assert _cluster_motion_vectors([(10, 10, 5.0, 5.0)]) == []
        # coherent but not actually moving
        assert _cluster_motion_vectors([(10 + i * 20, 10, 0.0, 0.0) for i in range(4)]) == []


class TestBearingAndSpeed:
    def test_bearing_uses_screen_coordinates(self):
        # Screen y grows downward, so north is -y.
        assert _bearing_from_vector(0, -1) == pytest.approx(0)     # N
        assert _bearing_from_vector(1, 0) == pytest.approx(90)     # E
        assert _bearing_from_vector(0, 1) == pytest.approx(180)    # S
        assert _bearing_from_vector(-1, 0) == pytest.approx(270)   # W

    def test_bearing_round_trips_through_compass(self):
        assert _deg_to_compass(_bearing_from_vector(1, -1)) == "NE"

    def test_speed_conversion(self):
        # 10 px at 0.5 km/px in 10 minutes = 5 km in 1/6 h = 30 km/h
        assert _storm_speed_kmh(10, 0.5, 10) == pytest.approx(30.0)

    def test_speed_guards_bad_inputs(self):
        assert _storm_speed_kmh(10, 0.5, 0) == 0.0
        assert _storm_speed_kmh(10, 0, 10) == 0.0


class TestPrecipArrival:
    @staticmethod
    def _mask(size, box):
        h, w = size
        alpha = np.zeros((h, w), dtype=np.uint8)
        y0, x0, y1, x1 = box
        alpha[y0:y1, x0:x1] = 255
        return Image.fromarray(np.dstack([np.zeros((h, w, 3), np.uint8), alpha]), "RGBA")

    def test_upwind_precip_gives_positive_eta(self):
        # Precip 100px west of home, moving east at 0.5 km/px, 60 km/h.
        mask = self._mask((200, 200), (90, 0, 110, 20))
        eta = _precip_arrival_minutes(mask, 1.0, 0.0, 60.0, 0.5, (100.0, 100.0))
        # nearest upwind pixel is at x=19 -> 81 px -> 40.5 km -> ~40.5 min
        assert eta == pytest.approx(40.5, abs=1.0)

    def test_precip_over_home_reports_now(self):
        mask = self._mask((200, 200), (95, 95, 105, 105))
        assert _precip_arrival_minutes(mask, 1.0, 0.0, 60.0, 0.5, (100.0, 100.0)) == 0.0

    def test_downwind_precip_never_arrives(self):
        # Precip is east of home and moving further east -- it has already passed.
        mask = self._mask((200, 200), (90, 180, 110, 200))
        assert _precip_arrival_minutes(mask, 1.0, 0.0, 60.0, 0.5, (100.0, 100.0)) is None

    def test_off_corridor_precip_ignored(self):
        # Upwind but far off the motion axis -- it will miss home entirely.
        mask = self._mask((200, 200), (0, 0, 10, 20))
        assert _precip_arrival_minutes(mask, 1.0, 0.0, 60.0, 0.5, (100.0, 100.0)) is None

    def test_empty_mask_and_zero_speed(self):
        mask = self._mask((200, 200), (0, 0, 0, 0))
        assert _precip_arrival_minutes(mask, 1.0, 0.0, 60.0, 0.5, (100.0, 100.0)) is None
        busy = self._mask((200, 200), (90, 0, 110, 20))
        assert _precip_arrival_minutes(busy, 1.0, 0.0, 0.0, 0.5, (100.0, 100.0)) is None


class TestNowcastOutline:
    def test_outline_is_drawn_as_dashes_not_fill(self):
        alpha = np.zeros((80, 80), dtype=np.uint8)
        alpha[20:60, 20:60] = 255
        nowcast = Image.fromarray(
            np.dstack([np.zeros((80, 80, 3), np.uint8), alpha]), "RGBA")
        img = Image.new("RGB", (80, 80), (255, 255, 255))
        assert _draw_nowcast_outline(img, nowcast) is True

        arr = np.array(img)
        black = np.all(arr == 0, axis=-1)
        # Edge is marked...
        assert black[20:60, 20:22].any()
        # ...the interior is left alone (an outline, not a filled blob)...
        assert not black[35:45, 35:45].any()
        # ...and it is dashed, so well under half the perimeter band is inked.
        assert black.sum() < 40 * 4 * 2

    def test_empty_nowcast_draws_nothing(self):
        nowcast = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
        img = Image.new("RGB", (40, 40), (255, 255, 255))
        assert _draw_nowcast_outline(img, nowcast) is False
        assert np.all(np.array(img) == 255)


class TestDrawCornerLabels:
    def test_plain_string_line_drawn_black(self):
        img = Image.new("RGB", (200, 100), (255, 255, 255))
        _draw_corner_labels(img, ["Radar: 3:14 PM"], {})
        colors = set(img.getdata())
        assert (0, 0, 0) in colors
        assert _STALE_COLOR not in colors

    def test_colored_tuple_line_survives_the_box_snap(self):
        img = Image.new("RGB", (200, 100), (255, 255, 255))
        _draw_corner_labels(img, [("Radar: 43 min old", _STALE_COLOR)], {})
        # The red text must still be red after the white/black luminance snap —
        # not flattened to black (its luminance is under 128) or lost entirely.
        # (The box border is legitimately black, so check for red presence only.)
        assert _STALE_COLOR in set(img.getdata())

    def test_mixed_black_and_red_lines_both_survive(self):
        img = Image.new("RGB", (200, 100), (255, 255, 255))
        _draw_corner_labels(
            img,
            ["Cells ENE 32 mph", ("Radar: 43 min old", _STALE_COLOR)],
            {},
        )
        colors = set(img.getdata())
        assert (0, 0, 0) in colors
        assert _STALE_COLOR in colors

    def test_no_lines_draws_nothing(self):
        img = Image.new("RGB", (200, 100), (255, 255, 255))
        _draw_corner_labels(img, [], {})
        assert set(img.getdata()) == {(255, 255, 255)}


class TestRadarStaleness:
    """The legend's 7 color swatches legitimately include red, so these tests
    must scope color checks to the right-side station/time label region rather
    than the whole canvas."""

    def _render_right_label(self, frame_ts, now):
        # Wide enough that the 7 left-aligned color swatches (which legitimately
        # include red/orange) don't reach anywhere near the right-aligned label.
        canvas = Image.new("RGB", (1200, 100), (255, 255, 255))
        with patch("modules.weather.time.time", return_value=now):
            _draw_seven_color_legend(canvas, frame_ts, "KOHX", {})
        y0 = canvas.height - _LEGEND_H
        return canvas.crop((canvas.width - 160, y0, canvas.width, canvas.height))

    def test_fresh_frame_shows_black_clock_time_not_red(self):
        now = 1_700_000_000
        region = self._render_right_label(now - 5 * 60, now)  # 5 min old
        colors = set(region.getdata())
        assert (0, 0, 0) in colors
        assert _STALE_COLOR not in colors

    def test_stale_frame_shows_red_age(self):
        now = 1_700_000_000
        region = self._render_right_label(now - 43 * 60, now)  # matches audit's example age
        # (The legend strip's top border is always a black line across the full
        # width, so black presence isn't a useful negative signal here — only
        # red presence, which only the stale path draws.)
        assert _STALE_COLOR in set(region.getdata())

    def test_boundary_at_threshold_is_not_stale(self):
        now = 1_700_000_000
        region = self._render_right_label(now - _STALE_RADAR_MIN * 60, now)  # exactly at threshold
        assert _STALE_COLOR not in set(region.getdata())


class TestDrawStaleBanner:
    def test_source_image_is_not_mutated(self):
        img = Image.new("RGB", (300, 100), (255, 255, 255))
        _draw_stale_banner(img, 43.0, {})
        assert set(img.getdata()) == {(255, 255, 255)}

    def test_banner_band_is_pure_red_and_white_only(self):
        img = Image.new("RGB", (300, 100), (255, 255, 255))
        banner = _draw_stale_banner(img, 43.0, {})
        band = banner.crop((0, 0, banner.width, _STALE_BANNER_H))
        colors = set(band.getdata())
        # Must be exact-palette in/out: text drawn on an already-quantized bmp
        # has to be snapped to {red, white}, or a later requantize pass would
        # scatter its anti-aliased edges onto unrelated palette colors.
        assert colors <= {_STALE_COLOR, (255, 255, 255)}
        assert _STALE_COLOR in colors

    def test_body_below_banner_is_untouched(self):
        img = Image.new("RGB", (300, 100), (0, 0, 0))
        banner = _draw_stale_banner(img, 5.0, {})
        below = banner.crop((0, _STALE_BANNER_H, banner.width, banner.height))
        assert set(below.getdata()) == {(0, 0, 0)}


class TestGenerateStaleFallback:
    def test_total_fetch_failure_pushes_cached_render_with_banner(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "radar").mkdir()
        cached = tmp_path / "radar" / "eink_quantized_display_KOHX.bmp"
        Image.new("RGB", (300, 100), (255, 255, 255)).save(cached, format="bmp")
        old = time.time() - 3600
        os.utime(cached, (old, old))

        with patch("modules.weather.generate_weather_image", return_value=(None, False, None)), \
             patch("modules.weather.get_special_weather_messages", return_value=None):
            result = W_MOD.generate({"station": {"name": "KOHX"}})

        assert result is not None
        assert os.path.basename(result) == "eink_stale_display_KOHX.bmp"
        out = Image.open(result).convert("RGB")
        assert _STALE_COLOR in set(out.crop((0, 0, out.width, _STALE_BANNER_H)).getdata())
        # The cached original that fed the banner is left untouched on disk.
        assert set(Image.open(cached).convert("RGB").getdata()) == {(255, 255, 255)}

    def test_cold_start_with_no_cache_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("modules.weather.generate_weather_image", return_value=(None, False, None)), \
             patch("modules.weather.get_special_weather_messages", return_value=None):
            assert W_MOD.generate({"station": {"name": "KOHX"}}) is None


class TestNowcastArrival:
    @staticmethod
    def _frames(now):
        return [{"time": int(now + 600), "path": "/n1"},
                {"time": int(now + 1200), "path": "/n2"}]

    def test_first_wet_frame_sets_eta(self):
        now = 1_700_000_000
        def fake_frame(path, *a, **kw):
            probe = 32
            alpha = np.zeros((probe, probe), dtype=np.uint8)
            if path == "/n2":
                alpha[:] = 255
            return Image.fromarray(
                np.dstack([np.zeros((probe, probe, 3), np.uint8), alpha]), "RGBA")
        with patch("modules.weather._fetch_rv_frame", side_effect=fake_frame):
            eta = _nowcast_arrival_minutes(self._frames(now), 36.0, -86.8, 7, 512, 4, {}, now)
        assert eta == pytest.approx(20.0)

    def test_dry_nowcast_returns_none(self):
        now = 1_700_000_000
        dry = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
        with patch("modules.weather._fetch_rv_frame", return_value=dry):
            assert _nowcast_arrival_minutes(self._frames(now), 36.0, -86.8, 7, 512, 4, {}, now) is None

    def test_no_frames_returns_none(self):
        assert _nowcast_arrival_minutes([], 36.0, -86.8, 7, 512, 4, {}, 0) is None


class TestOverlayClipping:
    """Overlays must never paint outside the radar region."""

    def test_alert_polygon_clipped_to_region(self):
        # A polygon spanning well past the region's right edge; the area to the
        # right of the region stands in for the conditions panel.
        canvas = Image.new("RGB", (400, 200), (255, 255, 255))
        # Starts inside the region and runs several degrees past its right edge.
        big = [(-86.85, 36.05), (-80.0, 36.05), (-80.0, 35.95), (-86.85, 35.95)]
        alerts = [{"event": "Tornado Warning", "severity": "Extreme", "polygon": big}]
        with patch("modules.weather._fetch_nws_alerts", return_value=alerts):
            _overlay_severe_alerts(canvas, 36.0, -86.8, 7, 200, 200, {}, config={})
        right = np.array(canvas)[:, 200:]
        assert np.all(right == 255), "alert overlay bled outside the radar region"
        assert not np.all(np.array(canvas)[:, :200] == 255), "nothing drawn in-region"

    def test_alert_label_is_two_colour_after_snap(self):
        canvas = Image.new("RGB", (300, 200), (255, 255, 255))
        poly = [(-86.9, 36.1), (-86.7, 36.1), (-86.7, 35.9), (-86.9, 35.9)]
        alerts = [{"event": "Tornado Warning", "severity": "Extreme", "polygon": poly}]
        with patch("modules.weather._fetch_nws_alerts", return_value=alerts):
            _overlay_severe_alerts(canvas, 36.0, -86.8, 7, 300, 200, {}, config={})
        colors = {tuple(c) for c in np.unique(np.array(canvas).reshape(-1, 3), axis=0)}
        # Anti-aliased white-on-red glyph edges must have been resolved: nothing
        # but white, red and the untouched background may survive.
        assert colors <= {(255, 255, 255), (255, 0, 0)}, f"unsnapped AA pixels: {colors}"

    def test_lightning_halo_clipped_to_region(self):
        canvas = Image.new("RGB", (400, 200), (255, 255, 255))
        # A strike right on the region's right edge -- its 12px halo would
        # otherwise spill onto the panel beside it.
        _, _, _, lon_max = _view_bounds(36.0, -86.8, 7, 200, 200)
        strikes = [{"lat": 36.0, "lon": lon_max - 0.001}]
        _draw_lightning_overlay(canvas, strikes, 36.0, -86.8, 7, 200, 200)
        assert np.all(np.array(canvas)[:, 200:] == 255)


class TestAlertSeverityStyle:
    """A Tornado Warning (Extreme) must not render identically to a Flood
    Advisory (Minor/Unknown) — severity should drive outline/label color."""

    _POLY = [(-86.9, 36.1), (-86.7, 36.1), (-86.7, 35.9), (-86.9, 35.9)]

    def _render(self, alerts):
        canvas = Image.new("RGB", (300, 200), (255, 255, 255))
        with patch("modules.weather._fetch_nws_alerts", return_value=alerts):
            _overlay_severe_alerts(canvas, 36.0, -86.8, 7, 300, 200, {}, config={})
        return canvas

    def test_extreme_uses_its_styled_color(self):
        canvas = self._render(
            [{"event": "Tornado Warning", "severity": "Extreme", "polygon": self._POLY}]
        )
        assert _ALERT_SEVERITY_STYLE["Extreme"]["color"] in set(canvas.getdata())

    def test_severe_uses_a_different_color_than_extreme(self):
        canvas = self._render(
            [{"event": "Severe Thunderstorm Warning", "severity": "Severe", "polygon": self._POLY}]
        )
        colors = set(canvas.getdata())
        assert _ALERT_SEVERITY_STYLE["Severe"]["color"] in colors
        assert _ALERT_SEVERITY_STYLE["Extreme"]["color"] not in colors

    def test_moderate_uses_its_styled_color(self):
        canvas = self._render(
            [{"event": "Flood Warning", "severity": "Moderate", "polygon": self._POLY}]
        )
        assert _ALERT_SEVERITY_STYLE["Moderate"]["color"] in set(canvas.getdata())

    def test_minor_severity_falls_back_to_default_not_extreme_styling(self):
        canvas = self._render(
            [{"event": "Flood Advisory", "severity": "Minor", "polygon": self._POLY}]
        )
        colors = set(canvas.getdata())
        assert _ALERT_DEFAULT_STYLE["color"] in colors
        assert _ALERT_SEVERITY_STYLE["Extreme"]["color"] not in colors

    def test_missing_severity_falls_back_to_default(self):
        canvas = self._render([{"event": "Special Weather Statement", "polygon": self._POLY}])
        assert _ALERT_DEFAULT_STYLE["color"] in set(canvas.getdata())

    def test_more_severe_alert_wins_zorder_regardless_of_input_order(self):
        minor = {"event": "Flood Watch", "severity": "Minor", "polygon": self._POLY}
        extreme = {"event": "Tornado Warning", "severity": "Extreme", "polygon": self._POLY}
        for alerts in ([minor, extreme], [extreme, minor]):
            # Identical polygon for both alerts, so their outline/label pixels
            # coincide — draw order (least-severe first, by design) decides
            # which color survives, regardless of the order alerts arrived in.
            canvas = self._render(alerts)
            assert _ALERT_SEVERITY_STYLE["Extreme"]["color"] in set(canvas.getdata())


class TestAlertAreaQuery:
    def test_area_query_used_when_view_geometry_known(self):
        captured = {}
        def fake_get(url, **kw):
            captured["url"] = url
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"features": []}
            return resp
        with patch("modules.weather._view_state_codes", return_value=["TN", "KY"]), \
             patch("modules.weather.requests.get", side_effect=fake_get):
            _fetch_nws_alerts(36.0, -86.8, {}, zoom=7, width=520, height=444)
        assert "area=TN,KY" in captured["url"]
        assert "point=" not in captured["url"]

    def test_falls_back_to_point_query_without_geometry(self):
        captured = {}
        def fake_get(url, **kw):
            captured["url"] = url
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"features": []}
            return resp
        with patch("modules.weather.requests.get", side_effect=fake_get):
            _fetch_nws_alerts(36.0, -86.8, {})
        assert "point=36.0000,-86.8000" in captured["url"]


class TestSecretRedaction:
    def test_client_secret_never_reaches_the_log(self, caplog):
        boom = RuntimeError("failed for url: https://x/?client_secret=SUPERSECRET&p=1")
        with patch("modules.weather.requests.get", side_effect=boom):
            with caplog.at_level("WARNING"):
                result = _fetch_lightning_strikes(36.0, -86.8, {}, "id", "SUPERSECRET")
        assert result == []
        assert "SUPERSECRET" not in caplog.text
        assert "***" in caplog.text

    def test_redact_handles_blank_secrets(self):
        assert _redact("nothing to hide", "", None or "") == "nothing to hide"


class TestFirstSet:
    def test_zero_is_a_valid_coordinate(self):
        # lat/lon of exactly 0.0 is the equator / prime meridian, not "missing".
        assert _first_set(None, 0.0) == 0.0
        assert _first_set(0.0, 12.0) == 0.0

    def test_falls_through_to_later_values(self):
        assert _first_set(None, None, 5) == 5
        assert _first_set(None, None) is None


class TestViewBounds:
    def test_bounds_bracket_the_centre(self):
        lat_min, lon_min, lat_max, lon_max = _view_bounds(36.0, -86.8, 7, 520, 444)
        assert lat_min < 36.0 < lat_max
        assert lon_min < -86.8 < lon_max

    def test_zoom_7_view_spans_roughly_a_hundred_km(self):
        # Sanity check on the projection: at zoom 7 with 512px tiles the scale is
        # ~0.5 km/px, so a 520px wide view spans a bit over 250 km of longitude.
        _, lon_min, _, lon_max = _view_bounds(36.0, -86.8, 7, 520, 444)
        km = (lon_max - lon_min) * 111.32 * math.cos(math.radians(36.0))
        assert 200 < km < 300


class TestTileCache:
    def test_second_fetch_serves_from_disk(self, tmp_path, monkeypatch):
        monkeypatch.setattr(W_MOD, "_TILE_CACHE_DIR", str(tmp_path / "tiles"))
        buf = io.BytesIO()
        Image.new("RGBA", (8, 8), (1, 2, 3, 255)).save(buf, format="PNG")
        payload = buf.getvalue()

        calls = []
        def fake_get(url, **kw):
            calls.append(url)
            resp = MagicMock()
            resp.status_code = 200
            resp.content = payload
            return resp

        args = ("/v2/radar/past_0", 0, 0, 0, 0, 7, 8, 4, {}, 0, 0, 8, 8)
        with patch("modules.weather.requests.get", side_effect=fake_get):
            first = W_MOD._fetch_rv_frame(*args)
            second = W_MOD._fetch_rv_frame(*args)
        assert len(calls) == 1, "second fetch should have hit the disk cache"
        assert list(first.getdata()) == list(second.getdata())

    def test_prune_keeps_newest(self, tmp_path, monkeypatch):
        cache = tmp_path / "tiles"
        cache.mkdir()
        monkeypatch.setattr(W_MOD, "_TILE_CACHE_DIR", str(cache))
        for i in range(10):
            p = cache / f"{i}.png"
            p.write_bytes(b"x")
            os.utime(p, (i, i))
        W_MOD._prune_tile_cache(max_files=4)
        assert sorted(int(p.stem) for p in cache.iterdir()) == [6, 7, 8, 9]
