"""
E-Ink Display — Main Dispatcher

Reads `active_module` from config.yml, calls that module's generate() function,
then pushes the resulting BMP to the hardware display (Linux only).

Usage:
    poetry run python main.py
"""

import hashlib
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


def _deep_merge(base: dict, overrides: dict) -> dict:
    result = dict(base)
    for k, v in overrides.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load_config(path: str = "config.yml") -> Dict[str, Any]:
    with open(path) as f:
        cfg = yaml.safe_load(f)
    bot_state_path = os.path.join(os.path.dirname(os.path.abspath(path)), "bot_state.json")
    if os.path.exists(bot_state_path):
        import json
        with open(bot_state_path) as f:
            cfg = _deep_merge(cfg, json.load(f))
    return cfg


def _compute_hash(output_path: str) -> str | None:
    """Return MD5 hex digest of the image file, or None on error."""
    try:
        with open(output_path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except OSError:
        return None


def _is_unchanged(output_path: str, new_hash: str) -> bool:
    """Return True if new_hash matches the last-pushed hash."""
    hash_path = output_path + ".last_hash"
    if os.path.exists(hash_path):
        with open(hash_path) as f:
            return f.read().strip() == new_hash
    return False


def _save_hash(output_path: str, new_hash: str) -> None:
    """Persist hash after a successful display push."""
    with open(output_path + ".last_hash", "w") as f:
        f.write(new_hash)


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
        new_hash = _compute_hash(output_path)
        if new_hash and _is_unchanged(output_path, new_hash):
            logger.info("Image unchanged — skipping display push.")
        else:
            if platform.system() == "Linux":
                from display import display_color_image
                display_color_image(output_path, model=config.get("display_model", "epd7in5_V2"))
                logger.info("Displayed on e-ink hardware.")
                if new_hash:
                    _save_hash(output_path, new_hash)
            else:
                logger.info("macOS — skipping hardware display. Image at: %s", output_path)
    else:
        logger.info("Module returned no output (no change or error).")


if __name__ == "__main__":
    main()
