"""
Module Cycler — meta-module that cycles through a configurable list of
modules, advancing to the next one each time generate() is called.

State is persisted in a JSON file so the cycle continues across restarts.
"""

import importlib
import json
import os


STATE_FILE = "data/cycler_state.json"

# Import MODULE_MAP to resolve module names to import paths
MODULE_MAP = {
    "weather":         "modules.weather",
    "text":            "modules.text_display",
    "saint_of_day":    "modules.saint_of_day",
    "wiki_image":      "modules.wiki_image",
    "movie_slideshow": "modules.movie_slideshow",
    "nasa_apod":       "modules.nasa_apod",
    "quote_of_day":    "modules.quote_of_day",
    "on_this_day":     "modules.on_this_day",
    "moon_phase":      "modules.moon_phase",
    "art_of_day":      "modules.art_of_day",
    "chess_puzzle":    "modules.chess_puzzle",
    "flight_radar":    "modules.flight_radar",
    "franklin_cam":    "modules.franklin_cam",
    "parking_garage":  "modules.parking_garage",
}


def _load_state(state_file):
    """Load the current cycle index from state file."""
    if os.path.exists(state_file):
        try:
            with open(state_file) as f:
                return json.load(f)
        except Exception:
            pass
    return {"index": 0}


def _save_state(state_file, state):
    """Save the cycle state to file."""
    os.makedirs(os.path.dirname(state_file) or ".", exist_ok=True)
    with open(state_file, "w") as f:
        json.dump(state, f)


def generate(config):
    """Run the next module in the cycle. Return output path."""
    cycler_cfg = config.get("module_cycler", {})
    modules = cycler_cfg.get("modules", ["weather"])
    state_file = cycler_cfg.get("state_file", STATE_FILE)

    if not modules:
        print("[cycler] No modules configured.")
        return None

    state = _load_state(state_file)
    index = state.get("index", 0) % len(modules)
    current_name = modules[index]

    module_path = MODULE_MAP.get(current_name)
    if not module_path:
        print(f"[cycler] Unknown module '{current_name}', skipping.")
        # Advance past the unknown module
        state["index"] = (index + 1) % len(modules)
        _save_state(state_file, state)
        return None

    print(f"[cycler] Running module {index + 1}/{len(modules)}: {current_name}")
    mod = importlib.import_module(module_path)
    output_path = mod.generate(config)

    # Advance to next module for the next call
    state["index"] = (index + 1) % len(modules)
    state["last_module"] = current_name
    _save_state(state_file, state)

    return output_path


if __name__ == "__main__":
    import yaml
    with open("config.yml") as f:
        cfg = yaml.safe_load(f)
    path = generate(cfg)
    print(f"Output: {path}")
