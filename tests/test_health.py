"""
Unit tests for per-module health tracking in utils.py.
"""

import sys
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from utils import load_health, record_health


class TestLoadHealth:
    def test_missing_file_returns_empty_dict(self, tmp_path):
        health_path = str(tmp_path / "missing.json")
        assert load_health(health_path) == {}

    def test_corrupt_file_returns_empty_dict(self, tmp_path):
        health_path = tmp_path / "corrupt.json"
        health_path.write_text("{not valid json")
        assert load_health(str(health_path)) == {}


class TestRecordHealth:
    def test_creates_parent_dirs(self, tmp_path):
        health_path = str(tmp_path / "a" / "b" / "health.json")
        record_health("weather", success=True, health_path=health_path)
        assert os.path.exists(health_path)

    def test_success_sets_timestamps_and_clears_failures(self, tmp_path):
        health_path = str(tmp_path / "health.json")
        record_health("weather", success=True, health_path=health_path)

        health = load_health(health_path)
        entry = health["weather"]
        assert entry["last_success_ts"] > 0
        assert entry["last_attempt_ts"] > 0
        assert entry["consecutive_failures"] == 0
        assert entry["last_error"] is None

    def test_failure_increments_consecutive_count(self, tmp_path):
        health_path = str(tmp_path / "health.json")
        record_health("weather", success=False, error="boom", health_path=health_path)
        record_health("weather", success=False, error="boom again", health_path=health_path)

        entry = load_health(health_path)["weather"]
        assert entry["consecutive_failures"] == 2
        assert entry["last_error"] == "boom again"
        assert "last_success_ts" not in entry

    def test_success_after_failures_resets_count(self, tmp_path):
        health_path = str(tmp_path / "health.json")
        record_health("weather", success=False, error="boom", health_path=health_path)
        record_health("weather", success=False, error="boom", health_path=health_path)
        record_health("weather", success=True, health_path=health_path)

        entry = load_health(health_path)["weather"]
        assert entry["consecutive_failures"] == 0
        assert entry["last_error"] is None
        assert "last_success_ts" in entry

    def test_tracks_modules_independently(self, tmp_path):
        health_path = str(tmp_path / "health.json")
        record_health("weather", success=True, health_path=health_path)
        record_health("moon_phase", success=False, error="fail", health_path=health_path)

        health = load_health(health_path)
        assert health["weather"]["consecutive_failures"] == 0
        assert health["moon_phase"]["consecutive_failures"] == 1
