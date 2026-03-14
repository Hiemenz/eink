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
from typing import Any, Dict

import yaml

# Ensure project root is on sys.path when called from a subdirectory
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from utils import MODULE_MAP, get_logger, validate_config

logger = get_logger("main")


def load_config(path: str = "config.yml") -> Dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f)


def main() -> None:
    config = load_config()
    validate_config(config)
    active = config.get("active_module", "weather")

    module_path = MODULE_MAP.get(active)
    if not module_path:
        logger.error("Unknown module '%s'. Valid options: %s", active, list(MODULE_MAP))
        sys.exit(1)

    logger.info("Running module: %s", active)
    mod = importlib.import_module(module_path)
    output_path = mod.generate(config)

    if output_path:
        logger.info("Generated image: %s", output_path)
        if platform.system() == "Linux":
            from display import display_color_image
            display_color_image(output_path)
            logger.info("Displayed on e-ink hardware.")
        else:
            logger.info("macOS — skipping hardware display. Image at: %s", output_path)
    else:
        logger.info("Module returned no output (no change or error).")


if __name__ == "__main__":
    main()
