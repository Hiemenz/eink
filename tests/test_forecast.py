"""
Unit tests for modules/forecast.py: text wrapping, forecast-block
construction, layout height calculation, font-size search, and the
NWS API fetch/parse logic.
"""

import sys
import os
from unittest.mock import patch, MagicMock

import pytest
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.forecast import (
    wrap_text,
    build_forecast_blocks,
    calculate_block_height,
    calculate_total_height,
    find_best_font_size,
    get_detailed_forecast,
)


def _draw():
    img = Image.new("RGB", (800, 480), "white")
    return ImageDraw.Draw(img)


def _font():
    return ImageFont.load_default()


class TestWrapText:
    def test_short_text_stays_one_line(self):
        draw = _draw()
        font = _font()
        lines = wrap_text("hello world", font, 1000, draw)
        assert lines == ["hello world"]

    def test_wraps_when_exceeding_max_width(self):
        draw = _draw()
        font = _font()
        text = " ".join(["word"] * 30)
        lines = wrap_text(text, font, 50, draw)
        assert len(lines) > 1
        # Every line should individually fit within max_width.
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            assert bbox[2] - bbox[0] <= 50 or len(line.split()) == 1

    def test_single_long_word_placed_on_own_line(self):
        draw = _draw()
        font = _font()
        lines = wrap_text("supercalifragilisticexpialidocious", font, 10, draw)
        assert lines == ["supercalifragilisticexpialidocious"]

    def test_empty_text_returns_no_lines(self):
        draw = _draw()
        font = _font()
        assert wrap_text("", font, 500, draw) == []


class TestBuildForecastBlocks:
    def test_builds_expected_number_of_blocks(self):
        forecast_data = {
            "periods": [
                {"name": "Tonight", "temperature": 60, "temperatureUnit": "F",
                 "shortForecast": "Clear", "detailedForecast": "Clear skies."},
                {"name": "Monday", "temperature": 75, "temperatureUnit": "F",
                 "shortForecast": "Sunny", "detailedForecast": "Sunny all day."},
            ]
        }
        blocks = build_forecast_blocks(forecast_data, num_periods=2)
        assert len(blocks) == 2
        assert blocks[0]["name"] == "Tonight"
        assert "60°F" in blocks[0]["subtitle"]
        assert blocks[0]["detail"] == "Clear skies."

    def test_respects_num_periods_limit(self):
        forecast_data = {"periods": [{"name": f"P{i}"} for i in range(10)]}
        blocks = build_forecast_blocks(forecast_data, num_periods=3)
        assert len(blocks) == 3

    def test_missing_fields_use_defaults(self):
        forecast_data = {"periods": [{}]}
        blocks = build_forecast_blocks(forecast_data, num_periods=1)
        assert blocks[0]["name"] == "Unknown"
        assert "N/A" in blocks[0]["subtitle"]

    def test_empty_periods_returns_empty_list(self):
        assert build_forecast_blocks({"periods": []}, 5) == []

    def test_missing_periods_key_returns_empty_list(self):
        assert build_forecast_blocks({}, 5) == []


class TestHeightCalculations:
    def test_block_height_is_positive(self):
        draw = _draw()
        font = _font()
        block = {"name": "Tonight", "subtitle": " 60°F - Clear", "detail": "Clear skies expected."}
        h = calculate_block_height(block, font, font, 400, draw, 2, 8)
        assert h > 0

    def test_total_height_scales_with_block_count(self):
        draw = _draw()
        font = _font()
        block = {"name": "Tonight", "subtitle": " 60°F - Clear", "detail": "Clear."}
        one_block_h = calculate_total_height([block], font, font, 400, draw)
        three_blocks_h = calculate_total_height([block, block, block], font, font, 400, draw)
        assert three_blocks_h > one_block_h
        assert three_blocks_h == pytest.approx(one_block_h * 3, rel=0.2)

    def test_empty_blocks_zero_height(self):
        draw = _draw()
        font = _font()
        assert calculate_total_height([], font, font, 400, draw) == 0

    def test_wrapping_increases_height_for_long_detail(self):
        draw = _draw()
        font = _font()
        short_block = {"name": "A", "subtitle": " x", "detail": "short"}
        long_block = {"name": "A", "subtitle": " x", "detail": "word " * 200}
        short_h = calculate_block_height(short_block, font, font, 200, draw, 2, 8)
        long_h = calculate_block_height(long_block, font, font, 200, draw, 2, 8)
        assert long_h > short_h


class TestFindBestFontSize:
    def test_returns_within_bounds(self):
        draw = _draw()
        blocks = [{"name": "Tonight", "subtitle": " 60°F - Clear",
                   "detail": "Clear skies expected overnight."}]
        # Use the real default font path so ImageFont.truetype succeeds;
        # if unavailable in the sandbox, fall back gracefully.
        font_path = "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"
        if not os.path.exists(font_path):
            pytest.skip("Liberation Mono font not available in this environment")
        size = find_best_font_size(blocks, font_path, 780, 460, draw,
                                    max_font_size=40, min_font_size=8)
        assert 8 <= size <= 40

    def test_bad_font_path_returns_min_size(self):
        draw = _draw()
        blocks = [{"name": "A", "subtitle": " x", "detail": "y"}]
        size = find_best_font_size(blocks, "/no/such/font.ttf", 780, 460, draw,
                                    max_font_size=40, min_font_size=8)
        assert size == 8

    def test_smaller_available_height_yields_smaller_or_equal_font(self):
        draw = _draw()
        font_path = "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"
        if not os.path.exists(font_path):
            pytest.skip("Liberation Mono font not available in this environment")
        blocks = [{"name": "Tonight", "subtitle": " 60°F - Clear",
                   "detail": "Clear skies expected overnight with light winds."}]
        big_h_size = find_best_font_size(blocks, font_path, 780, 1000, draw, 60, 8)
        small_h_size = find_best_font_size(blocks, font_path, 780, 100, draw, 60, 8)
        assert small_h_size <= big_h_size


class TestGetDetailedForecast:
    @patch("modules.forecast.requests.get")
    def test_successful_fetch_returns_location_and_periods(self, mock_get):
        points_resp = MagicMock()
        points_resp.raise_for_status.return_value = None
        points_resp.json.return_value = {
            "properties": {
                "forecast": "https://api.weather.gov/gridpoints/OHX/1,1/forecast",
                "relativeLocation": {
                    "properties": {"city": "Franklin", "state": "TN"}
                },
            }
        }
        forecast_resp = MagicMock()
        forecast_resp.raise_for_status.return_value = None
        forecast_resp.json.return_value = {
            "properties": {"periods": [{"name": "Tonight"}]}
        }
        mock_get.side_effect = [points_resp, forecast_resp]

        result = get_detailed_forecast(35.9, -86.8)
        assert result["location"] == "Franklin, TN"
        assert result["periods"] == [{"name": "Tonight"}]

    @patch("modules.forecast.requests.get")
    def test_request_exception_returns_none(self, mock_get):
        import requests
        mock_get.side_effect = requests.exceptions.RequestException("boom")
        assert get_detailed_forecast(35.9, -86.8) is None

    @patch("modules.forecast.requests.get")
    def test_malformed_response_missing_keys_returns_none(self, mock_get):
        points_resp = MagicMock()
        points_resp.raise_for_status.return_value = None
        points_resp.json.return_value = {"properties": {}}  # missing 'forecast'
        mock_get.return_value = points_resp
        assert get_detailed_forecast(35.9, -86.8) is None
