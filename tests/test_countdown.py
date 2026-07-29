"""
Unit tests for modules/countdown.py — pure date-math logic: date parsing,
next-event selection, and date formatting. No network calls in this module.
"""

import os
import sys
from datetime import date

import pytest

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
