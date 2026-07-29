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
