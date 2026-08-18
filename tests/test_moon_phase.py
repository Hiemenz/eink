"""
Unit tests for pure moon-phase math in modules/moon_phase.py.
"""

import sys
import os
from datetime import datetime, timezone

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.moon_phase import (
    _moon_age,
    _phase_fraction,
    _phase_name,
    _illumination,
    _days_until_full,
    _draw_moon,
    generate,
    _REF_NEW_MOON,
    _LUNAR_CYCLE,
)


class TestMoonAge:
    def test_at_reference_new_moon_age_is_zero(self):
        age = _moon_age(_REF_NEW_MOON)
        assert age == pytest.approx(0.0, abs=1e-6)

    def test_age_wraps_after_full_cycle_plus_offset(self):
        # One full lunar cycle plus 5 days should behave the same as just
        # 5 days past the reference new moon (i.e. it wraps around).
        from datetime import timedelta
        offset_only = _REF_NEW_MOON + timedelta(days=5)
        offset_plus_cycle = _REF_NEW_MOON + timedelta(days=_LUNAR_CYCLE + 5)
        age_a = _moon_age(offset_only)
        age_b = _moon_age(offset_plus_cycle)
        assert age_a == pytest.approx(age_b, abs=1e-6)

    def test_age_is_always_within_cycle_bounds(self):
        age = _moon_age(datetime(2026, 7, 28, tzinfo=timezone.utc))
        assert 0.0 <= age < _LUNAR_CYCLE

    def test_defaults_to_now_when_no_arg(self):
        age = _moon_age()
        assert 0.0 <= age < _LUNAR_CYCLE


class TestPhaseFraction:
    def test_zero_age_is_zero_fraction(self):
        assert _phase_fraction(0.0) == 0.0

    def test_half_cycle_age_is_half_fraction(self):
        assert _phase_fraction(_LUNAR_CYCLE / 2) == pytest.approx(0.5, abs=1e-9)

    def test_full_cycle_age_is_fraction_one(self):
        assert _phase_fraction(_LUNAR_CYCLE) == pytest.approx(1.0, abs=1e-9)


class TestKnownFullMoon:
    """
    A known historical full moon: 2000-01-21 04:41 UTC (per US Naval
    Observatory data, ~15 days after the reference new moon of 2000-01-06
    18:14 UTC). At this timestamp the phase fraction should be ~0.5 and
    illumination should be at (or very near) its 100% peak.
    """

    def test_known_full_moon_fraction_near_half(self):
        full_moon = datetime(2000, 1, 21, 4, 41, tzinfo=timezone.utc)
        age = _moon_age(full_moon)
        fraction = _phase_fraction(age)
        assert fraction == pytest.approx(0.5, abs=0.02)

    def test_known_full_moon_name_is_full(self):
        full_moon = datetime(2000, 1, 21, 4, 41, tzinfo=timezone.utc)
        fraction = _phase_fraction(_moon_age(full_moon))
        assert _phase_name(fraction) == "Full Moon"

    def test_known_full_moon_illumination_near_100(self):
        full_moon = datetime(2000, 1, 21, 4, 41, tzinfo=timezone.utc)
        fraction = _phase_fraction(_moon_age(full_moon))
        illum = _illumination(fraction)
        assert illum >= 95


class TestPhaseName:
    @pytest.mark.parametrize("fraction,expected", [
        (0.0, "New Moon"),
        (0.03, "New Moon"),
        (0.10, "Waxing Crescent"),
        (0.25, "First Quarter"),
        (0.35, "Waxing Gibbous"),
        (0.50, "Full Moon"),
        (0.60, "Waning Gibbous"),
        (0.75, "Last Quarter"),
        (0.90, "Waning Crescent"),
        (0.99, "Waning Crescent"),
    ])
    def test_phase_name_boundaries(self, fraction, expected):
        assert _phase_name(fraction) == expected

    def test_boundaries_are_contiguous_and_exhaustive(self):
        # Every fraction in [0, 1) must map to some valid phase name.
        valid_names = {
            "New Moon", "Waxing Crescent", "First Quarter", "Waxing Gibbous",
            "Full Moon", "Waning Gibbous", "Last Quarter", "Waning Crescent",
        }
        for i in range(1000):
            fraction = i / 1000.0
            assert _phase_name(fraction) in valid_names


class TestIllumination:
    def test_new_moon_is_zero_percent(self):
        assert _illumination(0.0) == 0

    def test_full_moon_is_hundred_percent(self):
        assert _illumination(0.5) == 100

    def test_quarter_moon_is_fifty_percent(self):
        assert _illumination(0.25) == pytest.approx(50, abs=1)

    def test_illumination_symmetric_waxing_waning(self):
        # Illumination should be the same value at the same distance from
        # new moon whether waxing or waning.
        assert _illumination(0.25) == _illumination(0.75)


class TestDaysUntilFull:
    def test_at_new_moon_days_to_full_is_half_cycle(self):
        days = _days_until_full(0.0)
        assert days == pytest.approx(_LUNAR_CYCLE / 2, abs=1e-9)

    def test_just_before_full_moon_days_to_full_approaches_zero(self):
        # Just shy of exact half-cycle (fraction < 0.5 branch), days-to-full
        # should approach zero.
        days = _days_until_full(_LUNAR_CYCLE / 2 - 1e-6)
        assert days == pytest.approx(0.0, abs=1e-3)

    def test_at_exact_full_moon_wraps_to_next_cycle(self):
        # At fraction == 0.5 exactly the function takes the >= 0.5 branch,
        # meaning "days until the *next* full moon" is a full cycle away
        # (today's full moon has already passed the boundary).
        days = _days_until_full(_LUNAR_CYCLE / 2)
        assert days == pytest.approx(_LUNAR_CYCLE, abs=1e-6)

    def test_just_after_full_moon_wraps_to_almost_full_cycle(self):
        age = _LUNAR_CYCLE / 2 + 0.01
        days = _days_until_full(age)
        assert days == pytest.approx(_LUNAR_CYCLE - 0.01, abs=1e-6)

    def test_days_until_full_always_non_negative(self):
        for i in range(100):
            age = _LUNAR_CYCLE * i / 100
            assert _days_until_full(age) >= 0


def _expected_draw_moon_size(radius):
    scale = 2
    r = radius * scale
    size = (r * 2 + 20) * scale
    canvas_size = int(size)
    return canvas_size // scale


class TestDrawMoon:
    def test_returns_rgba_image(self):
        img = _draw_moon(0.25, radius=100)
        assert img.mode == "RGBA"

    def test_output_size_matches_radius_formula(self):
        img = _draw_moon(0.5, radius=100)
        expected = _expected_draw_moon_size(100)
        assert img.size == (expected, expected)

    def test_default_radius_180(self):
        img = _draw_moon(0.5)
        expected = _expected_draw_moon_size(180)
        assert img.size == (expected, expected)

    @pytest.mark.parametrize("fraction", [0.0, 0.0625, 0.25, 0.5, 0.53125, 0.75, 0.9375, 0.999])
    def test_all_branch_boundaries_do_not_raise(self, fraction):
        # Covers the new-moon, waxing/waning terminator, and full-moon branches.
        img = _draw_moon(fraction, radius=60)
        assert img.size[0] > 0

    def test_new_moon_is_mostly_dark(self):
        img = _draw_moon(0.0, radius=80).convert("RGB")
        # Sample center pixel -- new moon fill is (40, 40, 50).
        cx, cy = img.width // 2, img.height // 2
        r, g, b = img.getpixel((cx, cy))
        assert r < 80 and g < 80 and b < 80

    def test_waxing_gibbous_is_bright_at_center(self):
        # 0.4 sits well inside the waxing-gibbous interior, clear of the
        # fraction=0.25 quarter-phase boundary where terminator_x == 0 and
        # the center pixel sits exactly on the anti-aliased seam.
        img = _draw_moon(0.4, radius=80).convert("RGB")
        cx, cy = img.width // 2, img.height // 2
        r, g, b = img.getpixel((cx, cy))
        assert r > 200 and g > 200

    def test_waning_gibbous_is_bright_at_center(self):
        # Regression test: the terminator ellipse's fill color used to be
        # fixed per lit_side (always dark for the waning/left branch), which
        # was only correct for the waning-crescent half of that branch. For
        # waning gibbous (0.5-0.75) it wrongly erased the lit center back to
        # dark. The ellipse color must instead depend on the sign of
        # cos(fraction * 2pi), not on lit_side.
        img = _draw_moon(0.6, radius=80).convert("RGB")
        cx, cy = img.width // 2, img.height // 2
        r, g, b = img.getpixel((cx, cy))
        assert r > 200 and g > 200

    def test_waxing_crescent_is_dark_at_center(self):
        # Mirror-image regression: waxing crescent (0.0625-0.25) used to
        # always extend lit color into the center via the same fixed-fill
        # ellipse, wrongly brightening what should be a mostly-dark disc.
        img = _draw_moon(0.15, radius=80).convert("RGB")
        cx, cy = img.width // 2, img.height // 2
        r, g, b = img.getpixel((cx, cy))
        assert r < 80 and g < 80

    def test_illumination_symmetric_around_full_moon(self):
        # Waxing and waning at the same distance from full moon (0.5) should
        # render an equally bright center -- the bug broke this symmetry.
        img_pre = _draw_moon(0.4, radius=80).convert("RGB")
        img_post = _draw_moon(0.6, radius=80).convert("RGB")
        cx, cy = img_pre.width // 2, img_pre.height // 2
        pre = img_pre.getpixel((cx, cy))
        post = img_post.getpixel((cx, cy))
        assert pre[0] > 200 and post[0] > 200


class TestGenerate:
    def test_generate_creates_file_at_configured_path(self, tmp_path):
        output_path = str(tmp_path / "moon.bmp")
        result = generate({"moon_phase": {"output_path": output_path}, "width": 800, "height": 480})
        assert result == output_path
        assert os.path.exists(output_path)

    def test_generate_uses_configured_canvas_size(self, tmp_path):
        output_path = str(tmp_path / "moon.bmp")
        generate({"moon_phase": {"output_path": output_path}, "width": 640, "height": 400})
        img = Image.open(output_path)
        assert img.size == (640, 400)

    def test_generate_default_canvas_size_800x480(self, tmp_path):
        output_path = str(tmp_path / "moon.bmp")
        generate({"moon_phase": {"output_path": output_path}})
        img = Image.open(output_path)
        assert img.size == (800, 480)

    def test_generate_default_output_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = generate({})
        assert result == "moon_display.bmp"
        assert os.path.exists(result)

    def test_generate_missing_moon_phase_section(self, tmp_path, monkeypatch):
        """No 'moon_phase' key at all in config must not raise."""
        monkeypatch.chdir(tmp_path)
        result = generate({"width": 800, "height": 480})
        assert os.path.exists(result)
