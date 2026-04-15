"""
Module Cycler — meta-module that cycles through a configurable list of
modules on a time-based interval.

Each call runs the current module. When `interval_minutes` has elapsed
since the last switch, it advances to the next module in the list.
State (index + timestamp) is persisted so the cycle survives restarts.

Config keys (under module_cycler:):
  modules:          list of module names to rotate through
  interval_minutes: how long to stay on each module (default: 60)
  state_file:       path to JSON state file (default: data/cycler_state.json)
"""

import importlib
import json
import os
import time

from utils import MODULE_MAP, get_logger

logger = get_logger("cycler")

STATE_FILE = "data/cycler_state.json"
DEFAULT_INTERVAL_MINUTES = 60


def _load_state(state_file):
    """Load cycle state (index + last_switched timestamp) from file."""
    if os.path.exists(state_file):
        try:
            with open(state_file) as f:
                return json.load(f)
        except Exception:
            pass
    return {"index": 0, "last_switched": 0}


def _save_state(state_file, state):
    """Persist cycle state to file."""
    os.makedirs(os.path.dirname(state_file) or ".", exist_ok=True)
    with open(state_file, "w") as f:
        json.dump(state, f)


def generate(config):
    """Run the current module; advance when interval_minutes has elapsed."""
    cycler_cfg = config.get("module_cycler", {})
    modules = cycler_cfg.get("modules", ["weather"])
    state_file = cycler_cfg.get("state_file", STATE_FILE)
    interval_minutes = float(cycler_cfg.get("interval_minutes", DEFAULT_INTERVAL_MINUTES))
    interval_seconds = interval_minutes * 60

    if not modules:
        logger.warning("No modules configured.")
        return None

    state = _load_state(state_file)
    index = state.get("index", 0) % len(modules)
    last_switched = state.get("last_switched", 0)
    now = time.time()
    elapsed = now - last_switched

    if last_switched == 0:
        # First run with timed rotation — start the clock without advancing.
        logger.info("Starting timed rotation at module %d/%d: %s (interval: %.0f min)",
                    index + 1, len(modules), modules[index], interval_minutes)
        state["last_switched"] = now
    elif elapsed >= interval_seconds:
        # Interval elapsed — advance to the next module.
        index = (index + 1) % len(modules)
        state["index"] = index
        state["last_switched"] = now
        logger.info("Switching to module %d/%d: %s (after %.1f min)",
                    index + 1, len(modules), modules[index], elapsed / 60)
    else:
        remaining = (interval_seconds - elapsed) / 60
        logger.info("Staying on module %d/%d: %s (%.1f min remaining)",
                    index + 1, len(modules), modules[index], remaining)

    current_name = modules[index]
    module_path = MODULE_MAP.get(current_name)
    if not module_path:
        logger.warning("Unknown module '%s', skipping.", current_name)
        state["index"] = (index + 1) % len(modules)
        state["last_switched"] = now
        _save_state(state_file, state)
        return None

    mod = importlib.import_module(module_path)
    output_path = mod.generate(config)

    state["last_module"] = current_name
    _save_state(state_file, state)

    return output_path


if __name__ == "__main__":
    import yaml
    with open("config.yml") as f:
        cfg = yaml.safe_load(f)
    path = generate(cfg)
    logger.info("Output: %s", path)
