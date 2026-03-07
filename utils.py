"""
Shared utilities for the e-ink display project.

Centralizes MODULE_MAP, font loading, logging, and config validation
so every module uses a single source of truth.
"""

import logging
import platform
import sys
from logging.handlers import RotatingFileHandler
from typing import Dict, List, Optional

from PIL import ImageFont

MODULE_MAP: Dict[str, str] = {
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
    "sudoku_puzzle":   "modules.sudoku_puzzle",
    "poem_of_day":     "modules.poem_of_day",
    "news_headlines":  "modules.news_headlines",
    "flight_radar":    "modules.flight_radar",
    "franklin_cam":    "modules.franklin_cam",
    "parking_garage":  "modules.parking_garage",
    "module_cycler":   "modules.module_cycler",
}

# Platform-aware font search chains
_REGULAR_FONTS = [
    "/Library/Fonts/Arial Unicode.ttf",                                    # macOS
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",                # macOS alt
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",     # Pi
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",                     # Pi fallback
]

_BOLD_FONTS = [
    "/System/Library/Fonts/Supplemental/DIN Alternate Bold.ttf",           # macOS
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",                   # macOS fallback
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",        # Pi
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",                # Pi fallback
]


def get_font(size: int, bold: bool = False, config: Optional[dict] = None) -> ImageFont.FreeTypeFont:
    """Load a TrueType font at the given size, with platform-aware fallbacks.

    Tries config-provided paths first, then platform defaults, then Pillow's
    built-in default font.
    """
    paths: List[str] = []
    if config:
        if bold and config.get("bold_font_path"):
            paths.append(config["bold_font_path"])
        if config.get("font_path"):
            paths.append(config["font_path"])

    paths.extend(_BOLD_FONTS if bold else _REGULAR_FONTS)

    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue

    return ImageFont.load_default()


_LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s %(message)s"
_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def get_logger(name: str) -> logging.Logger:
    """Return a named logger with consistent formatting.

    On Linux (Pi), also writes to a rotating log file.
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    # Console handler
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT))
    logger.addHandler(console)

    # File handler on Linux (Pi)
    if platform.system() == "Linux":
        for log_path in ["/var/log/eink.log", "eink.log"]:
            try:
                fh = RotatingFileHandler(log_path, maxBytes=5_000_000, backupCount=2)
                fh.setLevel(logging.DEBUG)
                fh.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT))
                logger.addHandler(fh)
                break
            except PermissionError:
                continue

    return logger


_REQUIRED_KEYS = ["width", "height", "active_module", "output_mode"]
_OPTIONAL_KEYS = ["station", "forecast_location", "radar_mode", "panel_width"]


def validate_config(config: dict) -> bool:
    """Check that required config keys exist. Log warnings for missing optional keys.

    Returns True if all required keys are present.
    """
    logger = get_logger("config")
    ok = True
    for key in _REQUIRED_KEYS:
        if key not in config:
            logger.error("Missing required config key: %s", key)
            ok = False
    for key in _OPTIONAL_KEYS:
        if key not in config:
            logger.warning("Missing optional config key: %s", key)
    return ok
