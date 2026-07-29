"""
Unit tests for modules/module_cycler.py: state persistence and the
time-based module-advancement logic in generate().
"""

import sys
import os
import json
import time
from unittest.mock import patch, MagicMock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.module_cycler import _load_state, _save_state, generate


class TestStatePersistence:
    def test_load_missing_file_returns_defaults(self, tmp_path):
        state_file = str(tmp_path / "missing.json")
        state = _load_state(state_file)
        assert state == {"index": 0, "last_switched": 0}

    def test_save_then_load_round_trip(self, tmp_path):
        state_file = str(tmp_path / "state.json")
        _save_state(state_file, {"index": 3, "last_switched": 12345})
        loaded = _load_state(state_file)
        assert loaded == {"index": 3, "last_switched": 12345}

    def test_load_corrupt_file_returns_defaults(self, tmp_path):
        state_file = tmp_path / "corrupt.json"
        state_file.write_text("{bad json")
        state = _load_state(str(state_file))
        assert state == {"index": 0, "last_switched": 0}

    def test_save_creates_parent_dirs(self, tmp_path):
        state_file = str(tmp_path / "a" / "b" / "state.json")
        _save_state(state_file, {"index": 0, "last_switched": 0})
        assert os.path.exists(state_file)


class TestGenerateCycling:
    def _config(self, tmp_path, modules, interval_minutes=60):
        return {
            "module_cycler": {
                "modules": modules,
                "state_file": str(tmp_path / "cycler_state.json"),
                "interval_minutes": interval_minutes,
            }
        }

    def test_no_modules_configured_returns_none(self, tmp_path):
        config = self._config(tmp_path, [])
        assert generate(config) is None

    def test_first_run_starts_clock_without_advancing(self, tmp_path):
        fake_mod = MagicMock()
        fake_mod.generate.return_value = "out.bmp"
        with patch("modules.module_cycler.MODULE_MAP", {"weather": "fake.weather"}), \
             patch("modules.module_cycler.importlib.import_module", return_value=fake_mod):
            config = self._config(tmp_path, ["weather"])
            result = generate(config)
        assert result == "out.bmp"
        state = _load_state(config["module_cycler"]["state_file"])
        assert state["index"] == 0
        assert state["last_switched"] > 0

    def test_stays_on_module_before_interval_elapses(self, tmp_path):
        state_file = str(tmp_path / "cycler_state.json")
        _save_state(state_file, {"index": 0, "last_switched": time.time()})
        fake_mod = MagicMock()
        fake_mod.generate.return_value = "out.bmp"
        with patch("modules.module_cycler.MODULE_MAP", {"a": "fake.a", "b": "fake.b"}), \
             patch("modules.module_cycler.importlib.import_module", return_value=fake_mod):
            config = self._config(tmp_path, ["a", "b"], interval_minutes=60)
            config["module_cycler"]["state_file"] = state_file
            generate(config)
        state = _load_state(state_file)
        assert state["index"] == 0  # unchanged, interval not elapsed

    def test_advances_after_interval_elapses(self, tmp_path):
        state_file = str(tmp_path / "cycler_state.json")
        _save_state(state_file, {"index": 0, "last_switched": time.time() - 4000})
        fake_mod = MagicMock()
        fake_mod.generate.return_value = "out.bmp"
        with patch("modules.module_cycler.MODULE_MAP", {"a": "fake.a", "b": "fake.b"}), \
             patch("modules.module_cycler.importlib.import_module", return_value=fake_mod):
            config = self._config(tmp_path, ["a", "b"], interval_minutes=60)
            config["module_cycler"]["state_file"] = state_file
            generate(config)
        state = _load_state(state_file)
        assert state["index"] == 1  # advanced to next module

    def test_wraps_around_at_end_of_list(self, tmp_path):
        state_file = str(tmp_path / "cycler_state.json")
        _save_state(state_file, {"index": 1, "last_switched": time.time() - 4000})
        fake_mod = MagicMock()
        fake_mod.generate.return_value = "out.bmp"
        with patch("modules.module_cycler.MODULE_MAP", {"a": "fake.a", "b": "fake.b"}), \
             patch("modules.module_cycler.importlib.import_module", return_value=fake_mod):
            config = self._config(tmp_path, ["a", "b"], interval_minutes=60)
            config["module_cycler"]["state_file"] = state_file
            generate(config)
        state = _load_state(state_file)
        assert state["index"] == 0  # wrapped back to first

    def test_unknown_module_skips_and_advances(self, tmp_path):
        with patch("modules.module_cycler.MODULE_MAP", {}):
            config = self._config(tmp_path, ["ghost_module"], interval_minutes=60)
            result = generate(config)
        assert result is None
        state = _load_state(config["module_cycler"]["state_file"])
        assert state["index"] == 0  # wraps since only one module

    def test_calls_generate_on_selected_module_with_config(self, tmp_path):
        fake_mod = MagicMock()
        fake_mod.generate.return_value = "path.bmp"
        with patch("modules.module_cycler.MODULE_MAP", {"weather": "modules.weather"}), \
             patch("modules.module_cycler.importlib.import_module", return_value=fake_mod) as mock_import:
            config = self._config(tmp_path, ["weather"])
            result = generate(config)
        mock_import.assert_called_once_with("modules.weather")
        fake_mod.generate.assert_called_once_with(config)
        assert result == "path.bmp"

    def test_index_out_of_range_in_state_wraps_via_modulo(self, tmp_path):
        state_file = str(tmp_path / "cycler_state.json")
        _save_state(state_file, {"index": 5, "last_switched": time.time()})
        fake_mod = MagicMock()
        fake_mod.generate.return_value = "out.bmp"
        with patch("modules.module_cycler.MODULE_MAP", {"a": "fake.a", "b": "fake.b"}), \
             patch("modules.module_cycler.importlib.import_module", return_value=fake_mod):
            config = self._config(tmp_path, ["a", "b"], interval_minutes=60)
            config["module_cycler"]["state_file"] = state_file
            result = generate(config)
        assert result == "out.bmp"
