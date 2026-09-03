"""
Unit tests for modules/system_health.py — vitals collection, duration/age
formatting, and the generate() end-to-end rendering with mocked psutil and
utils.load_health() data.
"""

import os
import sys
import time
from unittest.mock import patch, MagicMock

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules import system_health


class TestFmtDuration:
    def test_none_returns_na(self):
        assert system_health._fmt_duration(None) == "N/A"

    def test_minutes_only(self):
        assert system_health._fmt_duration(300) == "5m"

    def test_hours_and_minutes(self):
        assert system_health._fmt_duration(3 * 3600 + 4 * 60) == "3h 4m"

    def test_days_and_hours(self):
        assert system_health._fmt_duration(2 * 86400 + 5 * 3600) == "2d 5h"

    def test_negative_clamped_to_zero(self):
        assert system_health._fmt_duration(-50) == "0m"


class TestFmtAge:
    def test_none_returns_never(self):
        assert system_health._fmt_age(None) == "never"

    def test_zero_returns_never(self):
        assert system_health._fmt_age(0) == "never"

    def test_recent_timestamp_formats_as_ago(self):
        ts = time.time() - 300
        assert system_health._fmt_age(ts) == "5m ago"

    def test_future_timestamp_returns_just_now(self):
        assert system_health._fmt_age(time.time() + 1000) == "just now"


class TestCpuTemp:
    def test_reads_millidegrees_from_thermal_file(self, tmp_path):
        thermal = tmp_path / "temp"
        thermal.write_text("48123")
        with patch.object(system_health, "THERMAL_PATH", str(thermal)):
            assert system_health._cpu_temp_c() == 48.1

    def test_missing_file_returns_none(self, tmp_path):
        with patch.object(system_health, "THERMAL_PATH", str(tmp_path / "missing")):
            assert system_health._cpu_temp_c() is None

    def test_garbage_content_returns_none(self, tmp_path):
        thermal = tmp_path / "temp"
        thermal.write_text("not a number")
        with patch.object(system_health, "THERMAL_PATH", str(thermal)):
            assert system_health._cpu_temp_c() is None


class TestCollectVitals:
    def test_psutil_available_populates_all_fields(self, tmp_path):
        thermal = tmp_path / "temp"
        thermal.write_text("50000")
        mock_psutil = MagicMock()
        mock_psutil.cpu_percent.return_value = 12.3
        mock_psutil.virtual_memory.return_value = MagicMock(percent=34.5, available=642 * 1_048_576)
        mock_psutil.disk_usage.return_value = MagicMock(percent=61.0, free=12 * 1_073_741_824)
        mock_psutil.boot_time.return_value = time.time() - 3600

        with patch.object(system_health, "THERMAL_PATH", str(thermal)), \
             patch.dict(sys.modules, {"psutil": mock_psutil}):
            vitals = system_health._collect_vitals()

        assert vitals["cpu_percent"] == 12.3
        assert vitals["mem_percent"] == 34.5
        assert vitals["mem_available_mb"] == 642
        assert vitals["disk_percent"] == 61.0
        assert vitals["disk_free_gb"] == 12.0
        assert vitals["cpu_temp_c"] == 50.0
        assert vitals["uptime_s"] == pytest.approx(3600, abs=5)

    def test_psutil_import_failure_degrades_gracefully(self, tmp_path):
        with patch.object(system_health, "THERMAL_PATH", str(tmp_path / "missing")), \
             patch.dict(sys.modules, {"psutil": None}):
            vitals = system_health._collect_vitals()

        assert vitals["cpu_percent"] is None
        assert vitals["mem_percent"] is None
        assert vitals["disk_percent"] is None
        assert vitals["uptime_s"] is None
        assert vitals["cpu_temp_c"] is None


class TestGenerate:
    def _config(self, tmp_path):
        return {"system_health": {"output_path": str(tmp_path / "out.bmp")}}

    def test_renders_valid_bmp_at_expected_size(self, tmp_path):
        with patch("modules.system_health._collect_vitals", return_value=_BLANK_VITALS()), \
             patch("utils.load_health", return_value={}):
            output = system_health.generate(self._config(tmp_path))
        assert os.path.exists(output)
        img = Image.open(output)
        assert img.size == (system_health.W, system_health.H)

    def test_no_health_data_shows_placeholder(self, tmp_path):
        with patch("modules.system_health._collect_vitals", return_value=_BLANK_VITALS()), \
             patch("utils.load_health", return_value={}):
            output = system_health.generate(self._config(tmp_path))
        assert os.path.exists(output)

    def test_all_healthy_renders_ok_banner(self, tmp_path):
        health = {
            "weather": {"consecutive_failures": 0, "last_success_ts": time.time()},
            "river_height": {"consecutive_failures": 0, "last_success_ts": time.time()},
        }
        with patch("modules.system_health._collect_vitals", return_value=_BLANK_VITALS()), \
             patch("utils.load_health", return_value=health):
            output = system_health.generate(self._config(tmp_path))
        assert os.path.exists(output)

    def test_failing_modules_listed(self, tmp_path):
        health = {
            "weather": {"consecutive_failures": 3, "last_error": "Connection timeout",
                        "last_success_ts": time.time() - 600},
            "river_height": {"consecutive_failures": 0, "last_success_ts": time.time()},
        }
        with patch("utils.load_health", return_value=health), \
             patch("modules.system_health._collect_vitals", return_value=_BLANK_VITALS()):
            output = system_health.generate(self._config(tmp_path))
        assert os.path.exists(output)

    def test_creates_output_directory(self, tmp_path):
        nested = tmp_path / "nested" / "dir" / "out.bmp"
        config = {"system_health": {"output_path": str(nested)}}
        with patch("utils.load_health", return_value={}), \
             patch("modules.system_health._collect_vitals", return_value=_BLANK_VITALS()):
            output = system_health.generate(config)
        assert os.path.exists(output)
        assert output == str(nested)

    def test_default_output_path_used_when_unconfigured(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("utils.load_health", return_value={}), \
             patch("modules.system_health._collect_vitals", return_value=_BLANK_VITALS()):
            output = system_health.generate({})
        assert output == "images/system_health.bmp"
        assert os.path.exists(output)


def _BLANK_VITALS():
    return {
        "cpu_percent": None,
        "mem_percent": None,
        "mem_available_mb": None,
        "disk_percent": None,
        "disk_free_gb": None,
        "uptime_s": None,
        "cpu_temp_c": None,
    }
