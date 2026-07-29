"""
Unit tests for modules/special_weather.py

Covers the pure headline-extraction logic (get_alert_headline) and the
network-backed message fetch (get_special_weather_messages), mocked so no
real HTTP calls are made.
"""

import sys
import os
from unittest.mock import patch, MagicMock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.special_weather import get_special_weather_messages, get_alert_headline


class TestGetAlertHeadline:
    def test_empty_input_returns_empty_string(self):
        assert get_alert_headline("") == ""
        assert get_alert_headline(None) == ""

    def test_skips_zone_codes_and_picks_headline(self):
        messages = "TNZ027-TNC037-\nSpecial Weather Statement\nissued by NWS"
        assert get_alert_headline(messages) == "Special Weather Statement"

    def test_skips_national_weather_service_line(self):
        messages = "National Weather Service Nashville TN\nSevere Thunderstorm Warning"
        assert get_alert_headline(messages) == "Severe Thunderstorm Warning"

    def test_skips_time_am_pm_line(self):
        messages = "1230 PM CDT\nFlash Flood Watch in effect"
        assert get_alert_headline(messages) == "Flash Flood Watch in effect"

    def test_skips_blank_and_dash_only_lines(self):
        messages = "\n\n---\n...\nTornado Warning"
        assert get_alert_headline(messages) == "Tornado Warning"

    def test_truncates_to_80_chars(self):
        long_line = "A" * 120
        assert get_alert_headline(long_line) == long_line[:80]
        assert len(get_alert_headline(long_line)) == 80

    def test_all_lines_skippable_falls_back_to_first_nonblank(self):
        # Every line matches a skip pattern except none survive the first pass;
        # the second pass should return the first non-blank line verbatim.
        messages = "TNZ027\n1200 PM CDT"
        result = get_alert_headline(messages)
        assert result == "TNZ027"

    def test_only_blank_lines_returns_default(self):
        messages = "\n   \n\t\n"
        assert get_alert_headline(messages) == "Special Weather Alert"

    def test_case_insensitive_skip_matching(self):
        messages = "national weather service office\nActual Headline Text"
        assert get_alert_headline(messages) == "Actual Headline Text"


class TestGetSpecialWeatherMessages:
    @patch("modules.special_weather.requests.get")
    def test_non_200_status_returns_none(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_get.return_value = mock_resp

        assert get_special_weather_messages("http://example.com") is None

    @patch("modules.special_weather.requests.get")
    def test_extracts_and_strips_pre_tag_contents(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body><pre>Line one\n<b>Line two</b></pre></body></html>"
        mock_get.return_value = mock_resp

        result = get_special_weather_messages("http://example.com")
        assert result == "Line one\nLine two"

    @patch("modules.special_weather.requests.get")
    def test_multiple_pre_blocks_joined_with_blank_line(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<pre>First block</pre><div>ignored</div><pre>Second block</pre>"
        mock_get.return_value = mock_resp

        result = get_special_weather_messages("http://example.com")
        assert result == "First block\n\nSecond block"

    @patch("modules.special_weather.requests.get")
    def test_no_pre_tags_returns_none(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>No preformatted content here</body></html>"
        mock_get.return_value = mock_resp

        assert get_special_weather_messages("http://example.com") is None
