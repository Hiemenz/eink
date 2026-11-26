"""
Unit tests for modules/special_weather.py

Covers alert selection by level, the banner headline, and the location-based
api.weather.gov request (mocked so no real network calls are made).
"""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from modules.special_weather import alert_headline, get_local_alert, pick_alert

FRANKLIN = {"forecast_location": {"latitude": 35.9251, "longitude": -86.8689}}


def _feature(event, **props):
    return {"properties": {"event": event, **props}}


class TestPickAlert:
    def test_each_level_is_recognised(self):
        for level in ("Warning", "Watch", "Advisory"):
            assert pick_alert([_feature(f"Flood {level}")])["level"] == level

    def test_most_urgent_wins_regardless_of_order(self):
        features = [_feature("Wind Advisory"), _feature("Tornado Warning"),
                    _feature("Flood Watch")]
        assert pick_alert(features)["event"] == "Tornado Warning"
        assert pick_alert(features[::-1])["event"] == "Tornado Warning"

    def test_statements_tests_and_cancellations_are_ignored(self):
        assert pick_alert([
            _feature("Special Weather Statement"),
            _feature("Tornado Warning", status="Test"),
            _feature("Flood Warning", messageType="Cancel"),
        ]) is None

    def test_nothing_active(self):
        assert pick_alert([]) is None


class TestAlertHeadline:
    def test_includes_end_time(self):
        alert = {"event": "Wind Advisory", "ends": "2001-01-01T21:00:00-06:00"}
        assert alert_headline(alert) == "Wind Advisory until Mon 9:00 PM"

    def test_without_end_time(self):
        assert alert_headline({"event": "Wind Advisory", "ends": None}) == "Wind Advisory"


class TestGetLocalAlert:
    @patch("modules.special_weather.requests.get")
    def test_queries_the_configured_location(self, get):
        resp = MagicMock()
        resp.json.return_value = {"features": [_feature("Winter Weather Advisory")]}
        get.return_value = resp
        assert get_local_alert(FRANKLIN, {})["level"] == "Advisory"
        assert get.call_args[0][0].endswith("point=35.9251,-86.8689")

    @patch("modules.special_weather.requests.get", side_effect=OSError("down"))
    def test_network_failure_returns_none(self, get):
        assert get_local_alert(FRANKLIN, {}) is None

    @patch("modules.special_weather.requests.get")
    def test_disabled_or_unlocated_makes_no_request(self, get):
        assert get_local_alert({**FRANKLIN, "check_special_weather": False}, {}) is None
        assert get_local_alert({}, {}) is None
        get.assert_not_called()
