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

MODULE_INTERVALS: Dict[str, int] = {
    # Seconds between refreshes for each module.
    # Modules not listed fall back to config["update_interval"].
    "weather":          300,    # 5 min — radar + conditions
    "flight_radar":     300,    # 5 min — OpenSky data
    "sports_scores":    300,    # 5 min — live scores
    "air_quality":     3600,    # 1 hour — AQI changes slowly
    "parking_garage":   600,    # 10 min
    "crypto_market":    600,    # 10 min
    "franklin_cam":     120,    # 2 min — live camera
    "game_of_life":     300,    # 5 min per generation step
    "moon_phase":      3600,    # 1 hour
    "countdown":       3600,    # 1 hour — date math, no API
    "forecast_graph":  1800,    # 30 min — hourly forecast
    "aurora":          1800,    # 30 min — Kp index
    "pollen":          3600,    # 1 hour
    "word_of_day":    86400,    # 24 hours — changes daily
    "poem_of_day":    86400,
    "on_this_day":    86400,
    "art_of_day":     86400,
    "nasa_apod":      86400,
    "chess_puzzle":   86400,
    "sudoku_puzzle":  86400,
    "quote_of_day":   86400,
    "saint_of_day":   86400,
    "wiki_image":     86400,
    "news_headlines":  1800,    # 30 min
    "claude_news":     1800,
    "interesting_fact": 3600,
    "questions":        900,
    "brain_status":     300,
    "movie_slideshow":   60,    # advances each cycle
    "iss_tracker":       300,    # 5 min — ISS moves fast
    "earthquakes":       600,    # 10 min — USGS feed
    "stocks":            300,    # 5 min — market data
    "xkcd":            86400,    # 24 hours — daily comic
    "carbon_intensity": 1800,    # 30 min — grid mix
    "now_playing":        60,    # 1 min — track changes
    "traffic":           600,    # 10 min — incident data
    "agenda":            900,    # 15 min — calendar events
    "river_height":      900,    # 15 min — USGS gauge updates
    "business_idea":    86400,    # 24 hours — one new idea per day
}

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
    "brain_status":    "modules.brain_status",
    "claude_news":     "modules.claude_news",
    "questions":       "modules.questions",
    "interesting_fact": "modules.interesting_fact",
    "qrcode_display":  "modules.qrcode_display",
    "terminal":        "modules.terminal",
    "crypto_market":   "modules.crypto_market",
    "game_of_life":    "modules.game_of_life",
    "air_quality":     "modules.air_quality",
    "countdown":       "modules.countdown",
    "sports_scores":   "modules.sports_scores",
    "word_of_day":     "modules.word_of_day",
    "forecast_graph":  "modules.forecast_graph",
    "aurora":          "modules.aurora",
    "pollen":          "modules.pollen",
    "iss_tracker":     "modules.iss_tracker",
    "earthquakes":     "modules.earthquakes",
    "stocks":          "modules.stocks",
    "xkcd":            "modules.xkcd",
    "carbon_intensity": "modules.carbon_intensity",
    "now_playing":     "modules.now_playing",
    "traffic":         "modules.traffic",
    "agenda":          "modules.agenda",
    "river_height":    "modules.river_height",
    "business_idea":   "modules.business_idea",
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
