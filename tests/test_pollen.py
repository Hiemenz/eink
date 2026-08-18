"""
Unit tests for modules/pollen.py.
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

from modules import pollen


class TestSeverity:
    def test_none_value_is_none(self):
        assert pollen._severity(None) is None

    def test_zero_is_none_tier(self):
        assert pollen._severity(0) is pollen.SEV_NONE

    def test_negative_is_none_tier(self):
        assert pollen._severity(-5) is pollen.SEV_NONE

    def test_just_below_ten_is_low(self):
        assert pollen._severity(9.9) is pollen.SEV_LOW

    def test_ten_is_moderate(self):
        assert pollen._severity(10) is pollen.SEV_MODERATE

    def test_just_below_fifty_is_moderate(self):
        assert pollen._severity(49.9) is pollen.SEV_MODERATE

    def test_fifty_is_high(self):
        assert pollen._severity(50) is pollen.SEV_HIGH

    def test_five_hundred_is_high_inclusive(self):
        assert pollen._severity(500) is pollen.SEV_HIGH

    def test_above_five_hundred_is_very_high(self):
        assert pollen._severity(500.1) is pollen.SEV_VERY_HIGH


class TestSeverityRank:
    def test_ordering_is_monotonic(self):
        ranks = [pollen._severity_rank(t) for t in pollen.SEV_ORDER]
        assert ranks == sorted(ranks)

    def test_none_is_lowest_rank(self):
        assert pollen._severity_rank(pollen.SEV_NONE) == 0

    def test_very_high_is_highest_rank(self):
        assert pollen._severity_rank(pollen.SEV_VERY_HIGH) == len(pollen.SEV_ORDER) - 1


class TestCurrentHourIndex:
    def test_matches_current_hour(self, monkeypatch):
        from datetime import datetime

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2024, 6, 1, 14, 30)

        monkeypatch.setattr(pollen, "datetime", FixedDateTime)
        times = ["2024-06-01T13:00", "2024-06-01T14:00", "2024-06-01T15:00"]
        assert pollen._current_hour_index(times) == 1

    def test_no_match_falls_back_to_zero(self, monkeypatch):
        from datetime import datetime

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2024, 6, 1, 23, 0)

        monkeypatch.setattr(pollen, "datetime", FixedDateTime)
        times = ["2024-06-01T13:00", "2024-06-01T14:00"]
        assert pollen._current_hour_index(times) == 0

    def test_empty_times_returns_zero(self):
        assert pollen._current_hour_index([]) == 0
        assert pollen._current_hour_index(None) == 0


class TestFetchPollen:
    @patch("modules.pollen.requests.get")
    def test_success_extracts_current_hour_values(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "hourly": {
                "time": ["2024-06-01T00:00", "2024-06-01T01:00"],
                "alder_pollen": [5.0, 12.0],
                "birch_pollen": [None, None],
            }
        }
        mock_get.return_value = resp
        result = pollen._fetch_pollen(35.0, -86.0)
        assert result["alder_pollen"] in (5.0, 12.0)  # depends on current-hour index
        assert "fetched_at" in result

    @patch("modules.pollen.requests.get")
    def test_null_series_falls_back_to_first_non_null(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "hourly": {
                "time": ["2024-06-01T00:00"],
                "grass_pollen": [None],
            }
        }
        mock_get.return_value = resp
        # Force current-hour index beyond series length so initial lookup is None,
        # triggering the "first non-null" fallback (which is also None here).
        result = pollen._fetch_pollen(35.0, -86.0)
        assert result["grass_pollen"] is None

    @patch("modules.pollen.requests.get")
    def test_missing_species_key_is_none(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"hourly": {"time": []}}
        mock_get.return_value = resp
        result = pollen._fetch_pollen(35.0, -86.0)
        for key, _name in pollen.SPECIES:
            assert result[key] is None

    @patch("modules.pollen.requests.get")
    def test_network_failure_returns_none(self, mock_get):
        mock_get.side_effect = ConnectionError("boom")
        assert pollen._fetch_pollen(35.0, -86.0) is None

    @patch("modules.pollen.requests.get")
    def test_http_error_returns_none(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception("500")
        mock_get.return_value = resp
        assert pollen._fetch_pollen(35.0, -86.0) is None


class TestCacheTTL:
    def test_fresh_cache_skips_network(self, tmp_path):
        cache_dir = str(tmp_path)
        payload = {"alder_pollen": 5.0, "fetched_at": time.time()}
        with open(pollen._cache_path(cache_dir), "w") as f:
            json.dump(payload, f)

        with patch("modules.pollen._fetch_pollen") as mock_fetch:
            data = pollen._get_pollen_data(35.0, -86.0, cache_dir)
            mock_fetch.assert_not_called()
        assert data["alder_pollen"] == 5.0

    def test_expired_cache_triggers_fetch(self, tmp_path):
        cache_dir = str(tmp_path)
        payload = {"alder_pollen": 5.0, "fetched_at": time.time() - pollen.CACHE_TTL - 100}
        with open(pollen._cache_path(cache_dir), "w") as f:
            json.dump(payload, f)

        new_data = {"alder_pollen": 99.0, "fetched_at": time.time()}
        with patch("modules.pollen._fetch_pollen", return_value=new_data):
            data = pollen._get_pollen_data(35.0, -86.0, cache_dir)
        assert data["alder_pollen"] == 99.0

    def test_fetch_failure_falls_back_to_stale_cache(self, tmp_path):
        cache_dir = str(tmp_path)
        payload = {"alder_pollen": 5.0, "fetched_at": time.time() - pollen.CACHE_TTL - 100}
        with open(pollen._cache_path(cache_dir), "w") as f:
            json.dump(payload, f)

        with patch("modules.pollen._fetch_pollen", return_value=None):
            data = pollen._get_pollen_data(35.0, -86.0, cache_dir)
        assert data["alder_pollen"] == 5.0

    def test_fetch_failure_no_cache_returns_none(self, tmp_path):
        cache_dir = str(tmp_path)
        with patch("modules.pollen._fetch_pollen", return_value=None):
            data = pollen._get_pollen_data(35.0, -86.0, cache_dir)
        assert data is None


class TestFmtValue:
    def test_none_is_dash(self):
        assert pollen._fmt_value(None) == "—"

    def test_zero_is_zero_string(self):
        assert pollen._fmt_value(0) == "0"

    def test_small_value_has_one_decimal(self):
        assert pollen._fmt_value(5.234) == "5.2"

    def test_large_value_has_no_decimal(self):
        assert pollen._fmt_value(123.7) == "124"


class TestGenerate:
    """End-to-end generate() — no prior test exercised the public entry point."""

    def _config(self, tmp_path):
        return {
            "pollen": {
                "output_path": str(tmp_path / "pollen.bmp"),
                "cache_dir": str(tmp_path / "cache"),
            },
            "forecast_location": {"latitude": 35.9251, "longitude": -86.8689, "name": "Nashville"},
        }

    def test_no_data_renders_failure_screen(self, tmp_path):
        config = self._config(tmp_path)
        with patch("modules.pollen._get_pollen_data", return_value=None):
            result = pollen.generate(config)
        assert result == config["pollen"]["output_path"]
        assert os.path.exists(result)
        from PIL import Image
        img = Image.open(result)
        assert img.size == (pollen.WIDTH, pollen.HEIGHT)

    def test_all_species_present_renders_full_card_grid(self, tmp_path):
        config = self._config(tmp_path)
        data = {key: 12.5 for key, _name in pollen.SPECIES}
        data["fetched_at"] = time.time()
        with patch("modules.pollen._get_pollen_data", return_value=data):
            result = pollen.generate(config)
        from PIL import Image
        img = Image.open(result)
        assert img.size == (pollen.WIDTH, pollen.HEIGHT)

    def test_all_species_null_renders_no_data_note(self, tmp_path):
        config = self._config(tmp_path)
        data = {key: None for key, _name in pollen.SPECIES}
        data["fetched_at"] = time.time()
        with patch("modules.pollen._get_pollen_data", return_value=data):
            result = pollen.generate(config)
        assert os.path.exists(result)

    def test_missing_forecast_location_uses_default_coords(self, tmp_path):
        config = {"pollen": {"output_path": str(tmp_path / "pollen.bmp"),
                              "cache_dir": str(tmp_path / "cache")}}
        with patch("modules.pollen._get_pollen_data", return_value=None) as mock_get:
            pollen.generate(config)
        args, _ = mock_get.call_args
        assert args[0] == 35.9251
        assert args[1] == -86.8689

    def test_default_output_path_used_when_not_configured(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = {"pollen": {"cache_dir": str(tmp_path / "cache")}}
        with patch("modules.pollen._get_pollen_data", return_value=None):
            result = pollen.generate(config)
        assert result == "images/pollen_display.bmp"
        assert os.path.exists(result)
