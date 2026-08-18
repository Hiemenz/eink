"""
Unit tests for modules/countdown.py — pure date-math logic: date parsing,
next-event selection, and date formatting. No network calls in this module.
"""

import os
import sys
from datetime import date

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules import countdown


class TestParseDate:
    def test_valid_date(self):
        assert countdown._parse_date("2026-08-15") == date(2026, 8, 15)

    def test_strips_whitespace(self):
        assert countdown._parse_date("  2026-08-15  ") == date(2026, 8, 15)

    def test_invalid_format_returns_none(self):
        assert countdown._parse_date("08/15/2026") is None

    def test_empty_string_returns_none(self):
        assert countdown._parse_date("") is None

    def test_none_input_returns_none(self):
        assert countdown._parse_date(None) is None

    def test_garbage_returns_none(self):
        assert countdown._parse_date("not-a-date") is None


class TestPickNextEvent:
    def test_picks_smallest_positive_days(self):
        today = date(2026, 7, 21)
        events = [
            {"name": "Far", "date": "2026-12-25"},
            {"name": "Near", "date": "2026-07-25"},
        ]
        event, days = countdown._pick_next_event(events, today)
        assert event["name"] == "Near"
        assert days == 4

    def test_ignores_past_events(self):
        today = date(2026, 7, 21)
        events = [
            {"name": "Past", "date": "2026-01-01"},
            {"name": "Future", "date": "2026-08-01"},
        ]
        event, days = countdown._pick_next_event(events, today)
        assert event["name"] == "Future"

    def test_all_past_returns_none(self):
        today = date(2026, 7, 21)
        events = [{"name": "Past", "date": "2026-01-01"}]
        event, days = countdown._pick_next_event(events, today)
        assert event is None
        assert days is None

    def test_today_is_zero_days(self):
        today = date(2026, 7, 21)
        events = [{"name": "Today", "date": "2026-07-21"}]
        event, days = countdown._pick_next_event(events, today)
        assert event["name"] == "Today"
        assert days == 0

    def test_tie_returns_first_in_list_order(self):
        today = date(2026, 7, 21)
        events = [
            {"name": "First", "date": "2026-07-25"},
            {"name": "Second", "date": "2026-07-25"},
        ]
        event, days = countdown._pick_next_event(events, today)
        assert event["name"] == "First"

    def test_invalid_date_skipped(self):
        today = date(2026, 7, 21)
        events = [
            {"name": "Bad", "date": "not-a-date"},
            {"name": "Good", "date": "2026-07-25"},
        ]
        event, days = countdown._pick_next_event(events, today)
        assert event["name"] == "Good"

    def test_empty_events_list_returns_none(self):
        event, days = countdown._pick_next_event([], date(2026, 7, 21))
        assert event is None
        assert days is None


class TestShortDate:
    def test_formats_no_leading_zero(self):
        assert countdown._short_date(date(2026, 7, 1)) == "Jul 1"

    def test_double_digit_day(self):
        assert countdown._short_date(date(2026, 12, 25)) == "Dec 25"


class TestRender:
    def test_output_matches_canvas_size(self, tmp_path):
        out = str(tmp_path / "countdown.bmp")
        event = {"name": "Vacation", "date": "2026-08-15"}
        result = countdown._render(event, 10, date(2026, 8, 5), out, {})
        assert result == out
        img = Image.open(out)
        assert img.size == (countdown.WIDTH, countdown.HEIGHT)

    def test_zero_days_renders_today_banner(self, tmp_path):
        out = str(tmp_path / "today.bmp")
        event = {"name": "Now", "date": "2026-08-05"}
        # days_left=0 takes the "TODAY!" string branch — must not raise.
        countdown._render(event, 0, date(2026, 8, 5), out, {})
        assert os.path.exists(out)

    def test_progress_bar_drawn_when_start_date_present(self, tmp_path):
        out = str(tmp_path / "progress.bmp")
        event = {"name": "Trip", "date": "2026-08-15", "start_date": "2026-08-01"}
        countdown._render(event, 10, date(2026, 8, 5), out, {})
        img = Image.open(out).convert("RGB")
        # Some pixel in the reserved progress-bar band should be non-white
        # (the outline/fill), proving the bar actually got drawn.
        band = img.crop((0, 0, countdown.WIDTH, countdown.HEIGHT)).getdata()
        assert any(px != (255, 255, 255) for px in band)

    def test_custom_accent_color_used_for_name(self, tmp_path):
        out = str(tmp_path / "accent.bmp")
        event = {"name": "X", "date": "2026-08-15", "color": [255, 0, 0]}
        countdown._render(event, 10, date(2026, 8, 5), out, {})
        img = Image.open(out).convert("RGB")
        colors = {px for px in img.getdata()}
        assert (255, 0, 0) in colors

    def test_invalid_color_falls_back_to_default(self, tmp_path):
        out = str(tmp_path / "badcolor.bmp")
        event = {"name": "X", "date": "2026-08-15", "color": "not-a-color"}
        # Must not raise despite a malformed color value.
        countdown._render(event, 10, date(2026, 8, 5), out, {})
        assert os.path.exists(out)

    def test_missing_name_falls_back_to_default_label(self, tmp_path):
        out = str(tmp_path / "noname.bmp")
        event = {"date": "2026-08-15"}
        result = countdown._render(event, 10, date(2026, 8, 5), out, {})
        assert result == out

    def test_creates_parent_directories(self, tmp_path):
        out = str(tmp_path / "nested" / "deep" / "countdown.bmp")
        event = {"name": "X", "date": "2026-08-15"}
        countdown._render(event, 10, date(2026, 8, 5), out, {})
        assert os.path.exists(out)


class TestRenderNoEvents:
    def test_creates_placeholder_image(self, tmp_path):
        out = str(tmp_path / "none.bmp")
        result = countdown._render_no_events(out, {})
        assert result == out
        img = Image.open(out)
        assert img.size == (countdown.WIDTH, countdown.HEIGHT)


class TestGenerate:
    def test_no_events_configured_uses_fallback(self, tmp_path):
        out = str(tmp_path / "fallback.bmp")
        config = {"countdown": {"output_path": out, "events": []}}
        result = countdown.generate(config)
        assert result == out
        assert os.path.exists(out)

    def test_missing_countdown_section_uses_default_output_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = countdown.generate({})
        assert result == "images/countdown_display.bmp"
        assert os.path.exists(result)

    def test_all_events_in_past_renders_no_events_placeholder(self, tmp_path):
        out = str(tmp_path / "past.bmp")
        config = {
            "countdown": {
                "output_path": out,
                "events": [{"name": "Old", "date": "2000-01-01"}],
            }
        }
        result = countdown.generate(config)
        assert result == out
        assert os.path.exists(out)

    def test_picks_and_renders_nearest_future_event(self, tmp_path):
        out = str(tmp_path / "next.bmp")
        far_future = date.today().replace(year=date.today().year + 5).isoformat()
        near_future = date.today().replace(year=date.today().year + 1).isoformat()
        config = {
            "countdown": {
                "output_path": out,
                "events": [
                    {"name": "Far", "date": far_future},
                    {"name": "Near", "date": near_future},
                ],
            }
        }
        result = countdown.generate(config)
        assert result == out
        assert os.path.exists(out)
