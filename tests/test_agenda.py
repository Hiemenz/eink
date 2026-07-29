"""
Unit tests for modules/agenda.py — iCal parsing, recurrence, caching, and
grouping logic (no network calls; requests.get is mocked where needed).
"""

import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules import agenda


# ---------------------------------------------------------------------------
# iCal line unfolding / property splitting
# ---------------------------------------------------------------------------

class TestUnfoldLines:
    def test_no_folding(self):
        text = "BEGIN:VEVENT\nSUMMARY:Test\nEND:VEVENT"
        assert agenda._unfold_lines(text) == ["BEGIN:VEVENT", "SUMMARY:Test", "END:VEVENT"]

    def test_folded_continuation_with_space(self):
        text = "SUMMARY:Long tit\n le here\nEND:VEVENT"
        lines = agenda._unfold_lines(text)
        assert lines[0] == "SUMMARY:Long title here"

    def test_folded_continuation_with_tab(self):
        text = "SUMMARY:Long tit\n\tle here"
        lines = agenda._unfold_lines(text)
        assert lines[0] == "SUMMARY:Long title here"

    def test_crlf_normalized(self):
        text = "A:1\r\nB:2\r\n"
        assert agenda._unfold_lines(text) == ["A:1", "B:2", ""]


class TestSplitProp:
    def test_simple_prop(self):
        name, params, value = agenda._split_prop("SUMMARY:Meeting")
        assert name == "SUMMARY"
        assert params == {}
        assert value == "Meeting"

    def test_prop_with_params(self):
        name, params, value = agenda._split_prop(
            "DTSTART;TZID=America/New_York:20260721T093000"
        )
        assert name == "DTSTART"
        assert params == {"TZID": "America/New_York"}
        assert value == "20260721T093000"

    def test_no_colon_returns_none(self):
        name, params, value = agenda._split_prop("garbage line no colon")
        assert name is None
        assert params == {}
        assert value == ""


class TestUnescape:
    def test_escaped_comma_semicolon_backslash(self):
        assert agenda._unescape("a\\, b\\; c\\\\d") == "a, b; c\\d"

    def test_escaped_newline(self):
        assert agenda._unescape("line1\\nline2") == "line1 line2"

    def test_strips_whitespace(self):
        assert agenda._unescape("  hello  ") == "hello"


# ---------------------------------------------------------------------------
# Date/time parsing
# ---------------------------------------------------------------------------

class TestParseDt:
    def test_all_day_value_date(self):
        dt, all_day = agenda._parse_dt("20260721", {"VALUE": "DATE"})
        assert all_day is True
        assert dt == date(2026, 7, 21)

    def test_bare_8digit_treated_as_all_day(self):
        dt, all_day = agenda._parse_dt("20260721", {})
        assert all_day is True
        assert dt == date(2026, 7, 21)

    def test_timed_event_naive(self):
        dt, all_day = agenda._parse_dt("20260721T093000", {})
        assert all_day is False
        assert dt == datetime(2026, 7, 21, 9, 30, 0)

    def test_timed_event_utc_zulu(self):
        dt, all_day = agenda._parse_dt("20260721T093000Z", {})
        assert all_day is False
        assert dt.tzinfo is not None
        assert dt.astimezone(timezone.utc).hour == 9

    def test_empty_value_returns_none(self):
        dt, all_day = agenda._parse_dt("", {})
        assert dt is None
        assert all_day is False

    def test_garbage_value_returns_none(self):
        dt, all_day = agenda._parse_dt("not-a-date", {})
        assert dt is None
        assert all_day is False


class TestToLocalNaive:
    def test_naive_passthrough(self):
        dt = datetime(2026, 1, 1, 12, 0, 0)
        assert agenda._to_local_naive(dt) == dt

    def test_aware_converted_to_naive(self):
        dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = agenda._to_local_naive(dt)
        assert result.tzinfo is None


# ---------------------------------------------------------------------------
# Fallback VEVENT parser
# ---------------------------------------------------------------------------

class TestParseIcsFallback:
    def test_parses_single_event(self):
        text = (
            "BEGIN:VCALENDAR\n"
            "BEGIN:VEVENT\n"
            "SUMMARY:Team Meeting\n"
            "DTSTART:20260721T093000\n"
            "DTEND:20260721T103000\n"
            "LOCATION:Room 5\n"
            "END:VEVENT\n"
            "END:VCALENDAR\n"
        )
        events = agenda._parse_ics_fallback(text)
        assert len(events) == 1
        ev = events[0]
        assert ev["summary"] == "Team Meeting"
        assert ev["location"] == "Room 5"
        assert ev["start"] == datetime(2026, 7, 21, 9, 30)
        assert ev["all_day"] is False

    def test_skips_event_without_dtstart(self):
        text = "BEGIN:VEVENT\nSUMMARY:No start\nEND:VEVENT\n"
        events = agenda._parse_ics_fallback(text)
        assert events == []

    def test_multiple_events(self):
        text = (
            "BEGIN:VEVENT\nSUMMARY:One\nDTSTART:20260721T090000\nEND:VEVENT\n"
            "BEGIN:VEVENT\nSUMMARY:Two\nDTSTART:20260722T100000\nEND:VEVENT\n"
        )
        events = agenda._parse_ics_fallback(text)
        assert len(events) == 2
        assert [e["summary"] for e in events] == ["One", "Two"]

    def test_missing_summary_defaults_to_no_title(self):
        text = "BEGIN:VEVENT\nDTSTART:20260721T090000\nEND:VEVENT\n"
        events = agenda._parse_ics_fallback(text)
        assert events[0]["summary"] == "(No title)"

    def test_malformed_property_does_not_crash(self):
        # A DTSTART with unparsable garbage should just be skipped/ignored.
        text = (
            "BEGIN:VEVENT\nSUMMARY:Broken\nDTSTART:not-a-real-date\nEND:VEVENT\n"
            "BEGIN:VEVENT\nSUMMARY:Fine\nDTSTART:20260722T090000\nEND:VEVENT\n"
        )
        events = agenda._parse_ics_fallback(text)
        # Broken event has start=None -> finalize_event drops it.
        assert len(events) == 1
        assert events[0]["summary"] == "Fine"

    def test_rrule_captured(self):
        text = (
            "BEGIN:VEVENT\nSUMMARY:Standup\nDTSTART:20260701T090000\n"
            "RRULE:FREQ=DAILY;INTERVAL=1\nEND:VEVENT\n"
        )
        events = agenda._parse_ics_fallback(text)
        assert events[0]["rrule"] == "FREQ=DAILY;INTERVAL=1"


class TestParseIcs:
    def test_uses_fallback_when_no_library(self):
        text = "BEGIN:VEVENT\nSUMMARY:X\nDTSTART:20260721T090000\nEND:VEVENT\n"
        with patch.object(agenda, "_try_library_parse", return_value=None):
            events = agenda.parse_ics(text)
        assert len(events) == 1
        assert events[0]["summary"] == "X"


# ---------------------------------------------------------------------------
# Recurrence projection
# ---------------------------------------------------------------------------

class TestNextRecurringOccurrence:
    def test_daily_projects_into_window(self):
        ev = {
            "summary": "Standup",
            "start": datetime(2026, 7, 1, 9, 0),
            "all_day": False,
            "rrule": "FREQ=DAILY",
        }
        window_start = date(2026, 7, 20)
        window_end = date(2026, 7, 27)
        occ = agenda._next_recurring_occurrence(ev, window_start, window_end)
        assert occ is not None
        assert window_start <= occ["start"].date() <= window_end
        assert occ["rrule"] == ""

    def test_weekly_with_interval(self):
        ev = {
            "summary": "Biweekly sync",
            "start": datetime(2026, 7, 1, 9, 0),
            "all_day": False,
            "rrule": "FREQ=WEEKLY;INTERVAL=2",
        }
        window_start = date(2026, 7, 1)
        window_end = date(2026, 7, 1)
        occ = agenda._next_recurring_occurrence(ev, window_start, window_end)
        assert occ["start"] == datetime(2026, 7, 1, 9, 0)

    def test_unsupported_freq_returns_none(self):
        ev = {
            "summary": "Monthly",
            "start": datetime(2026, 7, 1, 9, 0),
            "all_day": False,
            "rrule": "FREQ=MONTHLY",
        }
        occ = agenda._next_recurring_occurrence(ev, date(2026, 7, 1), date(2026, 7, 31))
        assert occ is None

    def test_no_rrule_match_returns_none(self):
        ev = {"start": datetime(2026, 7, 1, 9, 0), "all_day": False, "rrule": ""}
        occ = agenda._next_recurring_occurrence(ev, date(2026, 7, 1), date(2026, 7, 31))
        assert occ is None

    def test_all_day_recurrence(self):
        ev = {
            "summary": "Holiday-ish",
            "start": date(2026, 7, 1),
            "all_day": True,
            "rrule": "FREQ=DAILY",
        }
        occ = agenda._next_recurring_occurrence(ev, date(2026, 7, 5), date(2026, 7, 10))
        assert occ is not None
        assert isinstance(occ["start"], date)
        assert date(2026, 7, 5) <= occ["start"] <= date(2026, 7, 10)

    def test_out_of_window_returns_none(self):
        ev = {
            "summary": "Far future",
            "start": datetime(2026, 7, 1, 9, 0),
            "all_day": False,
            "rrule": "FREQ=WEEKLY;INTERVAL=52",
        }
        occ = agenda._next_recurring_occurrence(ev, date(2026, 7, 8), date(2026, 7, 15))
        assert occ is None


# ---------------------------------------------------------------------------
# Serialization / formatting helpers
# ---------------------------------------------------------------------------

class TestFormatTime:
    def test_no_leading_zero(self):
        assert agenda._format_time(datetime(2026, 7, 21, 9, 30)) == "9:30 AM"

    def test_afternoon(self):
        assert agenda._format_time(datetime(2026, 7, 21, 14, 5)) == "2:05 PM"


class TestEventDay:
    def test_all_day_event(self):
        ev = {"start": date(2026, 7, 21)}
        assert agenda._event_day(ev) == date(2026, 7, 21)

    def test_timed_event(self):
        ev = {"start": datetime(2026, 7, 21, 9, 0)}
        assert agenda._event_day(ev) == date(2026, 7, 21)


class TestSerializeEvent:
    def test_timed_event(self):
        ev = {
            "summary": "Meeting",
            "location": "HQ",
            "start": datetime(2026, 7, 21, 9, 30),
            "all_day": False,
        }
        out = agenda._serialize_event(ev, 2)
        assert out["day"] == "2026-07-21"
        assert out["time_label"] == "9:30 AM"
        assert out["sort_key"] == "09:30"
        assert out["source_index"] == 2

    def test_all_day_event(self):
        ev = {"summary": "Holiday", "location": "", "start": date(2026, 7, 21), "all_day": True}
        out = agenda._serialize_event(ev, 0)
        assert out["time_label"] == "All day"
        assert out["sort_key"] == "00:00"
        assert out["all_day"] is True


class TestDayHeaderLabel:
    def test_today(self):
        today = date(2026, 7, 21)
        assert agenda._day_header_label(today, today) == "TODAY"

    def test_tomorrow(self):
        today = date(2026, 7, 21)
        assert agenda._day_header_label(today + timedelta(days=1), today) == "TOMORROW"

    def test_other_day(self):
        today = date(2026, 7, 21)
        label = agenda._day_header_label(today + timedelta(days=3), today)
        assert "Jul" in label


class TestGroupByDay:
    def test_groups_and_orders(self):
        events = [
            {"day": "2026-07-22", "summary": "B"},
            {"day": "2026-07-21", "summary": "A"},
            {"day": "2026-07-21", "summary": "A2"},
        ]
        groups = agenda._group_by_day(events)
        assert [d.isoformat() for d, _ in groups] == ["2026-07-21", "2026-07-22"]
        assert len(groups[0][1]) == 2

    def test_skips_invalid_day(self):
        events = [{"day": "not-a-date", "summary": "Bad"}]
        assert agenda._group_by_day(events) == []


# ---------------------------------------------------------------------------
# Cache read/write + TTL
# ---------------------------------------------------------------------------

class TestCache:
    def test_read_cache_missing_file(self, tmp_path):
        assert agenda._read_cache(str(tmp_path / "nope.json")) is None

    def test_write_then_read_cache(self, tmp_path):
        path = str(tmp_path / "cache.json")
        agenda._write_cache(path, [{"summary": "X"}])
        data = agenda._read_cache(path)
        assert data["events"] == [{"summary": "X"}]
        assert "ts" in data

    def test_read_cache_corrupt_json(self, tmp_path):
        path = tmp_path / "cache.json"
        path.write_text("{not json")
        assert agenda._read_cache(str(path)) is None


# ---------------------------------------------------------------------------
# _get_events — cache TTL / fallback behavior with mocked network fetch
# ---------------------------------------------------------------------------

class TestGetEvents:
    def test_fresh_cache_used_without_fetch(self, tmp_path, monkeypatch):
        cache_dir = str(tmp_path)
        config = {"agenda": {"cache_dir": cache_dir}}
        path = agenda._cache_path(config)
        agenda._write_cache(path, [{"summary": "Cached"}])

        with patch.object(agenda, "_collect_events") as mock_collect:
            events, status = agenda._get_events(config, ["http://example.com/cal.ics"], 7)
        mock_collect.assert_not_called()
        assert status == "ok"
        assert events == [{"summary": "Cached"}]

    def test_all_fetches_fail_falls_back_to_stale_cache(self, tmp_path):
        cache_dir = str(tmp_path)
        config = {"agenda": {"cache_dir": cache_dir}}
        path = agenda._cache_path(config)
        # Write a stale cache (timestamp far in the past).
        os.makedirs(cache_dir, exist_ok=True)
        with open(path, "w") as f:
            json.dump({"ts": 0, "events": [{"summary": "Stale"}]}, f)

        with patch.object(agenda, "_collect_events", return_value=([], False)):
            events, status = agenda._get_events(config, ["http://example.com/cal.ics"], 7)
        assert status == "stale"
        assert events == [{"summary": "Stale"}]

    def test_no_cache_all_fail_returns_unavailable(self, tmp_path):
        cache_dir = str(tmp_path)
        config = {"agenda": {"cache_dir": cache_dir}}
        with patch.object(agenda, "_collect_events", return_value=([], False)):
            events, status = agenda._get_events(config, ["http://example.com/cal.ics"], 7)
        assert status == "unavailable"
        assert events == []

    def test_successful_fetch_writes_cache(self, tmp_path):
        cache_dir = str(tmp_path)
        config = {"agenda": {"cache_dir": cache_dir}}
        with patch.object(
            agenda, "_collect_events", return_value=([{"summary": "New"}], True)
        ):
            events, status = agenda._get_events(config, ["http://example.com/cal.ics"], 7)
        assert status == "ok"
        assert events == [{"summary": "New"}]
        cached = agenda._read_cache(agenda._cache_path(config))
        assert cached["events"] == [{"summary": "New"}]


class TestCollectEvents:
    def test_fetch_failure_skips_source(self):
        with patch.object(agenda.requests, "get", side_effect=Exception("network down")):
            events, any_success = agenda._collect_events(["http://bad.example.com"], 7)
        assert events == []
        assert any_success is False

    def test_successful_fetch_parses_and_filters_window(self):
        today = date.today()
        ics_text = (
            f"BEGIN:VEVENT\nSUMMARY:In window\n"
            f"DTSTART:{today.strftime('%Y%m%d')}T090000\nEND:VEVENT\n"
        )
        mock_resp = MagicMock()
        mock_resp.text = ics_text
        mock_resp.raise_for_status.return_value = None
        with patch.object(agenda.requests, "get", return_value=mock_resp):
            events, any_success = agenda._collect_events(["http://example.com/cal.ics"], 7)
        assert any_success is True
        assert len(events) == 1
        assert events[0]["summary"] == "In window"

    def test_http_error_raised_skips_source(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("500 error")
        with patch.object(agenda.requests, "get", return_value=mock_resp):
            events, any_success = agenda._collect_events(["http://example.com/cal.ics"], 7)
        assert events == []
        assert any_success is False
