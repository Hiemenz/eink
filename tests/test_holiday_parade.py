"""Tests for the holiday balloon parade module (no network, no browser)."""

from datetime import date

import pytest
from PIL import Image

from modules import holiday_parade as H

INKS = {(0, 0, 0), (255, 255, 255), (0, 255, 0), (0, 0, 255), (255, 0, 0), (255, 255, 0), (255, 128, 0)}


def inks(image):
    return {color for _, color in image.convert("RGB").getcolors(100000)}


@pytest.mark.parametrize("day,expected", [
    (date(2026, 11, 26), "29 DAYS UNTIL CHRISTMAS"),
    (date(2026, 12, 24), "1 DAY UNTIL CHRISTMAS"),
    (date(2026, 12, 25), "MERRY CHRISTMAS!"),
    (date(2026, 12, 26), "364 DAYS UNTIL CHRISTMAS"),
])
def test_countdown(day, expected):
    assert H.christmas_countdown(day) == expected


@pytest.mark.parametrize("conditions,expected", [
    (None, "none"),
    ({"weather_code": 0}, "none"),
    ({"weather_code": 63}, "rain"),
    ({"weather_code": 73}, "snow"),
    ({"weather_code": 61, "precip_type": "Wintry Mix"}, "snow"),
])
def test_weather_classes(conditions, expected):
    assert H.classify_weather(conditions) == expected


def test_every_sprite_is_exported_in_panel_inks():
    names = [f"{b['file']}-{size}" for b in H.load_balloons() + [{"file": "santa"}]
             for size in ("small", "large")] + ["finale"]
    for name in names:
        sprite = Image.open(H.SPRITES / f"{name}.png").convert("RGBA")
        assert {c for _, c in sprite.getcolors(100000) if c[3]} <= {ink + (255,) for ink in INKS}, name


def _config(tmp_path, **extra):
    return {"holiday_parade": {"output_path": str(tmp_path / "out.bmp"), "weather": "snow", "night": "on",
                               "state_file": str(tmp_path / "state.json"), **extra}}


@pytest.mark.parametrize("mode", ["snapshot", "balloon_of_day"])
def test_generate_writes_a_panel_ink_picture(tmp_path, mode):
    path = H.generate(_config(tmp_path, mode=mode))
    image = Image.open(path)
    assert image.size == (800, 480)
    assert inks(image) <= INKS


def test_march_advances_and_wraps_after_santa(tmp_path):
    state = str(tmp_path / "state.json")
    assert [H._next_step(state, 2) for _ in range(4)] == [0, 1, 2, 0]


def test_every_snapshot_step_renders():
    balloons = H.load_balloons()
    for step in range(len(balloons) + 1):
        image = H.render_snapshot({}, balloons, step, "rain", False, date(2026, 12, 1), "Hi")
        assert inks(image) <= INKS
