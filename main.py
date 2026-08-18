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
from datetime import datetime, time as dt_time
from typing import Any, Dict

import yaml

# Ensure project root is on sys.path when called from a subdirectory
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from utils import MODULE_MAP, MODULE_INTERVALS, get_logger, record_health, validate_config

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


def _in_sleep_window(config: dict) -> bool:
    """Return True if the current time falls in the configured night sleep window."""
    sleep_cfg = config.get("night_sleep", {})
    if not sleep_cfg.get("enabled", False):
        return False
    try:
        h0, m0 = map(int, sleep_cfg.get("start", "23:00").split(":"))
        h1, m1 = map(int, sleep_cfg.get("end", "06:00").split(":"))
    except ValueError:
        return False
    now = datetime.now().time()
    start = dt_time(h0, m0)
    end = dt_time(h1, m1)
    if start <= end:
        return start <= now < end
    return now >= start or now < end  # window crosses midnight


def main() -> None:
    config = load_config()
    validate_config(config)
    active = config.get("active_module", "weather")

    if _in_sleep_window(config):
        logger.info("Night sleep window active — skipping refresh for module '%s'.", active)
        return

    module_path = MODULE_MAP.get(active)
    if not module_path:
        logger.error("Unknown module '%s'. Valid options: %s", active, list(MODULE_MAP))
        sys.exit(1)

    # Per-module update interval — prefer MODULE_INTERVALS over the global fallback
    interval = MODULE_INTERVALS.get(active, config.get("update_interval", 21600))
    config["_effective_interval"] = interval
    logger.info("Running module: %s (interval: %ds)", active, interval)
    mod = importlib.import_module(module_path)

    refresh_ok = True
    refresh_error = None
    try:
        output_path = mod.generate(config)

        if output_path:
            logger.info("Generated image: %s", output_path)
            new_hash = _compute_hash(output_path)
            if new_hash and _is_unchanged(output_path, new_hash):
                logger.info("Image unchanged — skipping display push.")
            else:
                if platform.system() == "Linux":
                    from display import display_color_image
                    pushed = display_color_image(
                        output_path,
                        model=config.get("display_model", "epd7in5_V2"),
                        full_clear_interval=int(config.get("full_clear_interval", 0)),
                    )
                    if pushed:
                        logger.info("Displayed on e-ink hardware.")
                        if new_hash:
                            _save_hash(output_path, new_hash)
                    else:
                        # Hash intentionally not saved, so the next run retries the push.
                        logger.error("Display push failed for module '%s' — see log above for hardware error.", active)
                        refresh_ok = False
                        refresh_error = "Display push failed (hardware error, see eink.log)"
                else:
                    logger.info("macOS — skipping hardware display. Image at: %s", output_path)
        else:
            logger.info("Module returned no output (no change or error).")
    except Exception as exc:
        logger.exception("Module '%s' crashed during refresh.", active)
        record_health(active, success=False, error=str(exc))
        raise

    record_health(active, success=refresh_ok, error=refresh_error)
    if not refresh_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
