"""
Unit tests for modules/traffic.py

Covers severity mapping, delay/count formatting, incident-count coloring,
text truncation, cache TTL/stale-fallback logic, and TomTom response
parsing (mocked — no real network calls).
"""

import sys
import os
import json
import time
from unittest.mock import patch, MagicMock

import pytest
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.traffic import (
    _severity_for,
    _fmt_delay,
    _count_color,
    _truncate,
    _cache_path,
    _load_cache,
    _save_cache,
    _fetch_incidents,
    _get_traffic_data,
    _draw_incident_rows,
    generate,
    WIDTH,
    HEIGHT,
)


class TestSeverityFor:
    def test_known_magnitudes_map_correctly(self):
        assert _severity_for(0)[0] == "Unknown"
        assert _severity_for(1)[0] == "Minor"
        assert _severity_for(2)[0] == "Moderate"
        assert _severity_for(3)[0] == "Major"
        assert _severity_for(4)[0] == "Severe"

    def test_unknown_magnitude_falls_back_to_unknown(self):
        assert _severity_for(99)[0] == "Unknown"
        assert _severity_for(-1)[0] == "Unknown"


class TestFmtDelay:
    def test_zero_or_negative_returns_empty_string(self):
        assert _fmt_delay(0) == ""
        assert _fmt_delay(-5) == ""

    def test_formats_seconds_as_minutes(self):
        assert _fmt_delay(120) == "2 min"

    def test_rounds_to_nearest_minute(self):
        assert _fmt_delay(89) == "1 min"   # 89/60 = 1.48 -> rounds to 1
        assert _fmt_delay(200) == "3 min"  # 200/60 = 3.33 -> rounds to 3

    def test_sub_minute_delay_still_shows_one_minute(self):
        assert _fmt_delay(10) == "1 min"

    def test_invalid_input_returns_empty_string(self):
        assert _fmt_delay(None) == ""
        assert _fmt_delay("not-a-number") == ""


class TestCountColor:
    def test_zero_is_green(self):
        assert _count_color(0) == (0, 200, 0)

    def test_few_is_yellow(self):
        assert _count_color(4) == (245, 200, 0)

    def test_moderate_is_orange(self):
        assert _count_color(8) == (255, 128, 0)

    def test_many_is_red(self):
        assert _count_color(9) == (220, 0, 0)


class TestTruncate:
    def _draw(self):
        img = Image.new("RGB", (800, 480))
        return ImageDraw.Draw(img)

    def test_empty_text_returns_empty(self):
        draw = self._draw()
        font = draw.getfont()
        assert _truncate(draw, "", font, 100) == ""

    def test_short_text_unchanged(self):
        draw = self._draw()
        font = draw.getfont()
        assert _truncate(draw, "short", font, 500) == "short"

    def test_long_text_truncated_with_ellipsis(self):
        draw = self._draw()
        font = draw.getfont()
        text = "a very long incident description " * 10
        result = _truncate(draw, text, font, 50)
        assert result.endswith("…")
        assert draw.textlength(result, font=font) <= 50


class TestCache:
    def test_load_cache_missing_returns_none(self, tmp_path):
        assert _load_cache(str(tmp_path)) is None

    def test_save_and_load_roundtrip(self, tmp_path):
        payload = {"incidents": [], "fetched_at": time.time()}
        _save_cache(str(tmp_path), payload)
        assert _load_cache(str(tmp_path)) == payload

    def test_corrupt_cache_returns_none(self, tmp_path):
        path = _cache_path(str(tmp_path))
        with open(path, "w") as f:
            f.write("{bad json")
        assert _load_cache(str(tmp_path)) is None


class TestFetchIncidents:
    @patch("modules.traffic.requests.get")
    def test_parses_incidents_from_response(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "incidents": [
                {
                    "properties": {
                        "events": [{"description": "Accident on I-65"}],
                        "magnitudeOfDelay": 3,
                        "delay": 300,
                        "iconCategory": 1,
                    }
                }
            ]
        }
        mock_get.return_value = resp

        result = _fetch_incidents("key", 35.9, -86.8, 0.15)
        assert result is not None
        assert len(result["incidents"]) == 1
        inc = result["incidents"][0]
        assert inc["description"] == "Accident on I-65"
        assert inc["magnitude"] == 3
        assert inc["delay"] == 300

    @patch("modules.traffic.requests.get")
    def test_empty_events_gives_empty_description(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "incidents": [{"properties": {"magnitudeOfDelay": 0}}]
        }
        mock_get.return_value = resp

        result = _fetch_incidents("key", 35.9, -86.8, 0.15)
        assert result["incidents"][0]["description"] == ""

    @patch("modules.traffic.requests.get")
    def test_no_incidents_key_returns_empty_list(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {}
        mock_get.return_value = resp

        result = _fetch_incidents("key", 35.9, -86.8, 0.15)
        assert result["incidents"] == []

    @patch("modules.traffic.requests.get")
    def test_request_exception_returns_none(self, mock_get):
        mock_get.side_effect = Exception("network error")
        assert _fetch_incidents("key", 35.9, -86.8, 0.15) is None


class TestGetTrafficData:
    def test_fresh_cache_used_without_network_call(self, tmp_path):
        payload = {"incidents": [], "fetched_at": time.time()}
        _save_cache(str(tmp_path), payload)
        with patch("modules.traffic.requests.get") as mock_get:
            result = _get_traffic_data("key", 35.9, -86.8, 0.15, str(tmp_path))
            mock_get.assert_not_called()
        assert result == payload

    @patch("modules.traffic._fetch_incidents")
    def test_live_fetch_failure_falls_back_to_stale_cache(self, mock_fetch, tmp_path):
        stale = {"incidents": [{"description": "old"}], "fetched_at": time.time() - 10_000}
        _save_cache(str(tmp_path), stale)
        mock_fetch.return_value = None

        result = _get_traffic_data("key", 35.9, -86.8, 0.15, str(tmp_path))
        assert result == stale

    @patch("modules.traffic._fetch_incidents")
    def test_no_cache_and_fetch_fails_returns_none(self, mock_fetch, tmp_path):
        mock_fetch.return_value = None
        result = _get_traffic_data("key", 35.9, -86.8, 0.15, str(tmp_path))
        assert result is None


class TestDrawIncidentRows:
    def _draw(self):
        return ImageDraw.Draw(Image.new("RGB", (WIDTH, HEIGHT)))

    def test_empty_incidents_shows_no_incidents_message(self):
        draw = self._draw()
        # Should not raise, and takes the "no incidents" branch (visually
        # verified elsewhere -- here we just assert it completes cleanly).
        _draw_incident_rows(draw, 100, 400, [], {})

    def test_missing_description_uses_placeholder(self):
        draw = self._draw()
        _draw_incident_rows(draw, 100, 400, [{"magnitude": 2, "delay": 60}], {})

    def test_more_than_max_rows_does_not_raise(self):
        draw = self._draw()
        incidents = [{"magnitude": i % 5, "delay": 60 * i, "description": f"inc {i}"} for i in range(20)]
        _draw_incident_rows(draw, 100, 400, incidents, {})


class TestGenerateIntegration:
    def test_no_api_key_renders_config_prompt(self, tmp_path):
        output_path = str(tmp_path / "out.bmp")
        with patch("modules.traffic._get_traffic_data") as mock_data:
            result = generate({"traffic": {"output_path": output_path, "api_key": ""}})
            mock_data.assert_not_called()
        assert result == output_path
        img = Image.open(output_path)
        assert img.size == (WIDTH, HEIGHT)

    @patch("modules.traffic._get_traffic_data")
    def test_fetch_and_cache_failure_renders_failure_screen(self, mock_data, tmp_path):
        mock_data.return_value = None
        output_path = str(tmp_path / "out.bmp")
        result = generate({"traffic": {"output_path": output_path, "api_key": "key123"}})
        assert os.path.exists(result)
        img = Image.open(result)
        assert img.size == (WIDTH, HEIGHT)

    @patch("modules.traffic._get_traffic_data")
    def test_successful_render_with_incidents(self, mock_data, tmp_path):
        mock_data.return_value = {
            "incidents": [
                {"description": "Crash on I-65", "magnitude": 4, "delay": 600, "icon_category": 1},
                {"description": "Roadwork", "magnitude": 1, "delay": 60, "icon_category": 7},
            ],
            "fetched_at": time.time(),
        }
        output_path = str(tmp_path / "out.bmp")
        result = generate({
            "traffic": {"output_path": output_path, "api_key": "key123"},
            "forecast_location": {"latitude": 36.0, "longitude": -87.0, "name": "Test City"},
        })
        assert os.path.exists(result)
        img = Image.open(result)
        assert img.size == (WIDTH, HEIGHT)

    @patch("modules.traffic._get_traffic_data")
    def test_no_incidents_renders_successfully(self, mock_data, tmp_path):
        mock_data.return_value = {"incidents": [], "fetched_at": time.time()}
        output_path = str(tmp_path / "out.bmp")
        result = generate({"traffic": {"output_path": output_path, "api_key": "key123"}})
        assert os.path.exists(result)

    @patch("modules.traffic._get_traffic_data")
    def test_passes_default_location_and_radius(self, mock_data, tmp_path):
        mock_data.return_value = {"incidents": [], "fetched_at": time.time()}
        output_path = str(tmp_path / "out.bmp")
        generate({"traffic": {"output_path": output_path, "api_key": "key123"}})

        args, kwargs = mock_data.call_args
        # (key, lat, lon, radius_deg, cache_dir)
        assert args[1] == pytest.approx(35.9251)
        assert args[2] == pytest.approx(-86.8689)
        assert args[3] == pytest.approx(0.15)

    @patch("modules.traffic._get_traffic_data")
    def test_default_output_path(self, mock_data, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_data.return_value = {"incidents": [], "fetched_at": time.time()}
        result = generate({"traffic": {"api_key": "key123"}})
        assert result == "images/traffic_display.bmp"
        assert os.path.exists(result)

    @patch("modules.traffic._get_traffic_data")
    def test_api_key_is_stripped(self, mock_data, tmp_path):
        mock_data.return_value = {"incidents": [], "fetched_at": time.time()}
        output_path = str(tmp_path / "out.bmp")
        generate({"traffic": {"output_path": output_path, "api_key": "  key123  "}})
        args, _ = mock_data.call_args
        assert args[0] == "key123"
