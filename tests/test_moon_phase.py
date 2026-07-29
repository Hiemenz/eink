"""
Unit tests for pure moon-phase math in modules/moon_phase.py.
"""

import sys
import os
from datetime import datetime, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.moon_phase import (
    _moon_age,
    _phase_fraction,
    _phase_name,
    _illumination,
    _days_until_full,
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
