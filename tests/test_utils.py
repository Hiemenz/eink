"""
Unit tests for shared utilities in utils.py.
"""

import sys
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from utils import MODULE_MAP, MODULE_INTERVALS, get_font, get_module_interval, validate_config


class TestModuleRegistrations:
    def test_module_map_nonempty(self):
        assert len(MODULE_MAP) > 0

    def test_module_intervals_nonempty(self):
        assert len(MODULE_INTERVALS) > 0

    def test_all_intervals_positive(self):
        for name, secs in MODULE_INTERVALS.items():
            assert secs > 0, f"MODULE_INTERVALS[{name!r}] = {secs} (must be > 0)"

    def test_weather_interval_is_5min(self):
        assert MODULE_INTERVALS["weather"] == 300

    def test_daily_modules_have_86400(self):
        for name in ("xkcd", "word_of_day", "nasa_apod", "chess_puzzle"):
            assert MODULE_INTERVALS.get(name) == 86400, (
                f"{name} should be 86400s (24h)"
            )


class TestGetModuleInterval:
    def test_per_module_override_wins_over_table(self):
        config = {"weather": {"update_interval": 999}}
        assert get_module_interval("weather", config) == 999

    def test_falls_back_to_module_intervals_table(self):
        config = {}
        assert get_module_interval("weather", config) == MODULE_INTERVALS["weather"]

    def test_falls_back_to_global_update_interval(self):
        config = {"update_interval": 1234}
        assert get_module_interval("not_a_real_module", config) == 1234

    def test_falls_back_to_hardcoded_default(self):
        config = {}
        assert get_module_interval("not_a_real_module", config) == 21600

    def test_module_cfg_without_update_interval_key_falls_through_to_table(self):
        config = {"weather": {"some_other_key": "value"}}
        assert get_module_interval("weather", config) == MODULE_INTERVALS["weather"]

    def test_module_cfg_that_is_not_a_dict_falls_through_to_table(self):
        config = {"weather": "not-a-dict"}
        assert get_module_interval("weather", config) == MODULE_INTERVALS["weather"]


class TestGetFont:
    def test_returns_font_object(self):
        from PIL import ImageFont
        font = get_font(16)
        assert isinstance(font, (ImageFont.FreeTypeFont, ImageFont.ImageFont))

    def test_bold_flag_accepted(self):
        from PIL import ImageFont
        font = get_font(16, bold=True)
        assert isinstance(font, (ImageFont.FreeTypeFont, ImageFont.ImageFont))


class TestValidateConfig:
    def test_valid_config_returns_true(self):
        cfg = {
            "width": 800,
            "height": 480,
            "active_module": "weather",
            "output_mode": "color",
        }
        assert validate_config(cfg) is True

    def test_missing_required_key_returns_false(self):
        cfg = {"width": 800, "height": 480, "active_module": "weather"}
        # output_mode is required but missing
        assert validate_config(cfg) is False

    def test_empty_config_returns_false(self):
        assert validate_config({}) is False
