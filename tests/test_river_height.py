"""
Unit tests for modules/river_height.py.

This module was recently added; per project lessons (radar/river cache and
threshold handling), we test the cache TTL / stale-fallback logic and the
stage-category threshold boundaries carefully.
"""

import json
import os
import sys
import time
from unittest.mock import patch, MagicMock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules import river_height as rh


class TestStageColorAndLabel:
    def test_below_all_thresholds_is_normal(self):
        thresholds = {"action_stage": 10, "flood_stage": 15, "moderate_flood_stage": 20, "major_flood_stage": 25}
        color, label = rh._stage_color_and_label(5.0, thresholds)
        assert label == "Normal"
        assert color == rh.NORMAL_COLOR

    def test_exactly_at_action_stage(self):
        thresholds = {"action_stage": 10, "flood_stage": 15, "moderate_flood_stage": 20, "major_flood_stage": 25}
        color, label = rh._stage_color_and_label(10.0, thresholds)
        assert label == "Action Stage"

    def test_between_action_and_flood(self):
        thresholds = {"action_stage": 10, "flood_stage": 15, "moderate_flood_stage": 20, "major_flood_stage": 25}
        color, label = rh._stage_color_and_label(12.0, thresholds)
        assert label == "Action Stage"

    def test_exactly_at_flood_stage(self):
        thresholds = {"action_stage": 10, "flood_stage": 15, "moderate_flood_stage": 20, "major_flood_stage": 25}
        color, label = rh._stage_color_and_label(15.0, thresholds)
        assert label == "Flood Stage"

    def test_exactly_at_moderate_flood(self):
        thresholds = {"action_stage": 10, "flood_stage": 15, "moderate_flood_stage": 20, "major_flood_stage": 25}
        color, label = rh._stage_color_and_label(20.0, thresholds)
        assert label == "Moderate Flood"

    def test_exactly_at_major_flood(self):
        thresholds = {"action_stage": 10, "flood_stage": 15, "moderate_flood_stage": 20, "major_flood_stage": 25}
        color, label = rh._stage_color_and_label(25.0, thresholds)
        assert label == "Major Flood"

    def test_above_major_flood(self):
        thresholds = {"action_stage": 10, "flood_stage": 15, "moderate_flood_stage": 20, "major_flood_stage": 25}
        color, label = rh._stage_color_and_label(100.0, thresholds)
        assert label == "Major Flood"

    def test_zero_thresholds_are_ignored(self):
        """A threshold of 0.0 (unconfigured) must never match, even at stage 0."""
        thresholds = {"action_stage": 0.0, "flood_stage": 0.0, "moderate_flood_stage": 0.0, "major_flood_stage": 0.0}
        color, label = rh._stage_color_and_label(0.0, thresholds)
        assert label == "Normal"

    def test_missing_threshold_keys_default_to_ignored(self):
        color, label = rh._stage_color_and_label(1000.0, {})
        assert label == "Normal"

    def test_checks_highest_category_first(self):
        # Stage qualifies for both moderate and major; major must win.
        thresholds = {"moderate_flood_stage": 5, "major_flood_stage": 5}
        color, label = rh._stage_color_and_label(5.0, thresholds)
        assert label == "Major Flood"


class TestCache:
    def test_load_cache_missing_returns_none(self, tmp_path):
        assert rh._load_cache(str(tmp_path)) is None

    def test_save_then_load_roundtrip(self, tmp_path):
        cache_dir = str(tmp_path)
        payload = {"current_ft": 3.5, "history": [], "fetched_at": time.time()}
        rh._save_cache(cache_dir, payload)
        loaded = rh._load_cache(cache_dir)
        assert loaded["current_ft"] == 3.5

    def test_corrupt_cache_returns_none(self, tmp_path):
        cache_dir = str(tmp_path)
        os.makedirs(cache_dir, exist_ok=True)
        with open(rh._cache_path(cache_dir), "w") as f:
            f.write("not json{{{")
        assert rh._load_cache(cache_dir) is None


class TestFetchUsgs:
    @patch("modules.river_height.requests.get")
    def test_success_parses_history_and_current(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "value": {
                "timeSeries": [{
                    "values": [{
                        "value": [
                            {"value": "3.10", "dateTime": "2024-01-01T00:00:00.000-06:00"},
                            {"value": "3.25", "dateTime": "2024-01-01T01:00:00.000-06:00"},
                        ]
                    }]
                }]
            }
        }
        mock_get.return_value = resp
        data = rh._fetch_usgs("03430500", 1)
        assert data["current_ft"] == 3.25
        assert len(data["history"]) == 2
        assert "fetched_at" in data

    @patch("modules.river_height.requests.get")
    def test_empty_timeseries_returns_none(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"value": {"timeSeries": []}}
        mock_get.return_value = resp
        assert rh._fetch_usgs("03430500", 1) is None

    @patch("modules.river_height.requests.get")
    def test_empty_values_returns_none(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "value": {"timeSeries": [{"values": [{"value": []}]}]}
        }
        mock_get.return_value = resp
        assert rh._fetch_usgs("03430500", 1) is None

    @patch("modules.river_height.requests.get")
    def test_malformed_readings_are_skipped(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "value": {
                "timeSeries": [{
                    "values": [{
                        "value": [
                            {"value": "not-a-number", "dateTime": "2024-01-01T00:00:00.000-06:00"},
                            {"value": "3.5", "dateTime": "2024-01-01T01:00:00.000-06:00"},
                        ]
                    }]
                }]
            }
        }
        mock_get.return_value = resp
        data = rh._fetch_usgs("03430500", 1)
        assert len(data["history"]) == 1
        assert data["current_ft"] == 3.5

    @patch("modules.river_height.requests.get")
    def test_all_readings_malformed_returns_none(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "value": {
                "timeSeries": [{
                    "values": [{
                        "value": [{"value": "nope", "dateTime": "2024-01-01T00:00:00.000-06:00"}]
                    }]
                }]
            }
        }
        mock_get.return_value = resp
        assert rh._fetch_usgs("03430500", 1) is None

    @patch("modules.river_height.requests.get")
    def test_missing_keys_returns_none(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"unexpected": "shape"}
        mock_get.return_value = resp
        assert rh._fetch_usgs("03430500", 1) is None

    @patch("modules.river_height.requests.get")
    def test_network_failure_returns_none(self, mock_get):
        mock_get.side_effect = ConnectionError("boom")
        assert rh._fetch_usgs("03430500", 1) is None

    @patch("modules.river_height.requests.get")
    def test_http_error_returns_none(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception("500")
        mock_get.return_value = resp
        assert rh._fetch_usgs("03430500", 1) is None


class TestGetRiverData:
    def test_fresh_cache_skips_network(self, tmp_path):
        cache_dir = str(tmp_path)
        payload = {"current_ft": 4.0, "history": [], "fetched_at": time.time()}
        rh._save_cache(cache_dir, payload)

        with patch("modules.river_height._fetch_usgs") as mock_fetch:
            data = rh._get_river_data({"site_number": "03430500"}, cache_dir)
            mock_fetch.assert_not_called()
        assert data["current_ft"] == 4.0

    def test_expired_cache_triggers_fetch(self, tmp_path):
        cache_dir = str(tmp_path)
        payload = {"current_ft": 4.0, "history": [], "fetched_at": time.time() - rh.CACHE_TTL - 10}
        rh._save_cache(cache_dir, payload)

        new_data = {"current_ft": 5.5, "history": [], "fetched_at": time.time()}
        with patch("modules.river_height._fetch_usgs", return_value=new_data):
            data = rh._get_river_data({"site_number": "03430500"}, cache_dir)
        assert data["current_ft"] == 5.5

    def test_fetch_failure_falls_back_to_stale_cache(self, tmp_path):
        cache_dir = str(tmp_path)
        payload = {"current_ft": 4.0, "history": [], "fetched_at": time.time() - rh.CACHE_TTL - 10}
        rh._save_cache(cache_dir, payload)

        with patch("modules.river_height._fetch_usgs", return_value=None):
            data = rh._get_river_data({"site_number": "03430500"}, cache_dir)
        assert data["current_ft"] == 4.0

    def test_fetch_failure_no_cache_returns_none(self, tmp_path):
        cache_dir = str(tmp_path)
        with patch("modules.river_height._fetch_usgs", return_value=None):
            data = rh._get_river_data({"site_number": "03430500"}, cache_dir)
        assert data is None

    def test_missing_site_number_returns_stale_cache_or_none(self, tmp_path):
        cache_dir = str(tmp_path)
        data = rh._get_river_data({"site_number": ""}, cache_dir)
        assert data is None

    def test_missing_site_number_with_stale_cache_returns_it(self, tmp_path):
        cache_dir = str(tmp_path)
        payload = {"current_ft": 4.0, "history": [], "fetched_at": time.time() - rh.CACHE_TTL - 10}
        rh._save_cache(cache_dir, payload)
        data = rh._get_river_data({"site_number": ""}, cache_dir)
        assert data["current_ft"] == 4.0


class TestResolveThresholds:
    def test_uses_config_values_when_present(self):
        cfg = {"action_stage": 10, "flood_stage": 15, "moderate_flood_stage": 20, "major_flood_stage": 25}
        thresholds = rh._resolve_thresholds(cfg)
        assert thresholds == {"action_stage": 10.0, "flood_stage": 15.0, "moderate_flood_stage": 20.0, "major_flood_stage": 25.0}

    def test_no_nws_lid_leaves_missing_as_zero(self):
        cfg = {"action_stage": 10}
        thresholds = rh._resolve_thresholds(cfg)
        assert thresholds["action_stage"] == 10.0
        assert thresholds["flood_stage"] == 0.0

    @patch("modules.river_height._fetch_nws_stages")
    def test_fills_missing_from_nws_when_lid_configured(self, mock_fetch):
        mock_fetch.return_value = {"flood_stage": 12.5, "action_stage": 8.0}
        cfg = {"nws_lid": "FKLT1"}
        thresholds = rh._resolve_thresholds(cfg)
        assert thresholds["flood_stage"] == 12.5
        assert thresholds["action_stage"] == 8.0
        mock_fetch.assert_called_once_with("FKLT1")

    @patch("modules.river_height._fetch_nws_stages")
    def test_config_values_take_priority_over_nws(self, mock_fetch):
        # NWS is still queried to fill in the *other* missing thresholds, but
        # a configured value must never be clobbered by the NWS response.
        mock_fetch.return_value = {"action_stage": 999.0, "flood_stage": 12.5}
        cfg = {"action_stage": 10.0, "nws_lid": "FKLT1"}
        thresholds = rh._resolve_thresholds(cfg)
        assert thresholds["action_stage"] == 10.0
        assert thresholds["flood_stage"] == 12.5

    @patch("modules.river_height._fetch_nws_stages")
    def test_nws_not_queried_when_nothing_missing(self, mock_fetch):
        cfg = {
            "action_stage": 10.0, "flood_stage": 15.0,
            "moderate_flood_stage": 20.0, "major_flood_stage": 25.0,
            "nws_lid": "FKLT1",
        }
        rh._resolve_thresholds(cfg)
        mock_fetch.assert_not_called()

    @patch("modules.river_height._fetch_nws_stages")
    def test_partial_nws_response_leaves_others_zero(self, mock_fetch):
        mock_fetch.return_value = {"action_stage": 8.0}
        cfg = {"nws_lid": "FKLT1"}
        thresholds = rh._resolve_thresholds(cfg)
        assert thresholds["action_stage"] == 8.0
        assert thresholds["flood_stage"] == 0.0


class TestFetchNwsStages:
    @patch("modules.river_height.requests.get")
    def test_parses_categories(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "flood": {
                "categories": [
                    {"name": "action", "stage": 8.0},
                    {"name": "minor", "stage": 12.0},
                    {"name": "moderate", "stage": 16.0},
                    {"name": "major", "stage": 20.0},
                ]
            }
        }
        mock_get.return_value = resp
        stages = rh._fetch_nws_stages("FKLT1")
        assert stages == {
            "action_stage": 8.0,
            "flood_stage": 12.0,
            "moderate_flood_stage": 16.0,
            "major_flood_stage": 20.0,
        }

    @patch("modules.river_height.requests.get")
    def test_network_failure_returns_empty_dict(self, mock_get):
        mock_get.side_effect = ConnectionError("boom")
        assert rh._fetch_nws_stages("FKLT1") == {}

    @patch("modules.river_height.requests.get")
    def test_malformed_response_returns_empty_dict(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"unexpected": "shape"}
        mock_get.return_value = resp
        assert rh._fetch_nws_stages("FKLT1") == {}

    @patch("modules.river_height.requests.get")
    def test_unknown_category_name_ignored(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "flood": {"categories": [{"name": "record", "stage": 30.0}]}
        }
        mock_get.return_value = resp
        assert rh._fetch_nws_stages("FKLT1") == {}
