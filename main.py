"""
E-Ink Display — Main Dispatcher

Reads `active_module` from config.yml, calls that module's generate() function,
then pushes the resulting BMP to the hardware display (Linux only).

Usage:
    poetry run python main.py
"""

import importlib
import platform
import sys
import os
import yaml

# Ensure project root is on sys.path when called from a subdirectory
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

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
    "module_cycler":   "modules.module_cycler",
    "brain_status":    "modules.brain_status",
}


def load_config(path="config.yml"):
    with open(path) as f:
        return yaml.safe_load(f)


def main():
    config = load_config()
    active = config.get("active_module", "weather")

    module_path = MODULE_MAP.get(active)
    if not module_path:
        print(f"[main] Unknown module '{active}'. Valid options: {list(MODULE_MAP)}")
        sys.exit(1)

    print(f"[main] Running module: {active}")
    mod = importlib.import_module(module_path)
    output_path = mod.generate(config)

    if output_path:
        print(f"[main] Generated image: {output_path}")
        if platform.system() == "Linux":
            from display import display_color_image
            display_color_image(output_path)
            print("[main] Displayed on e-ink hardware.")
        else:
            print(f"[main] macOS — skipping hardware display. Image at: {output_path}")
    else:
        print("[main] Module returned no output (no change or error).")


if __name__ == "__main__":
    main()
