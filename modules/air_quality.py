"""
Air Quality Index (AQI) Display Module — E-ink display showing current AQI
from the AirNow API for a configured ZIP code.

800×480 pixels, output as BMP.

Config section (add to config.yml):

    air_quality:
      output_path: images/aqi_display.bmp
      zip_code: "37064"
      api_key: ""          # get free key at airnow.gov
      cache_dir: data/

AQI tiers and display colors:
    0–50    Good           → Green  (0, 255, 0)
    51–100  Moderate       → Yellow (255, 255, 0)
    101–150 USG            → Orange (255, 128, 0)
    151–200 Unhealthy      → Red    (255, 0, 0)
    201–300 Very Unhealthy → Purple (150, 0, 150)
    301+    Hazardous      → Black  (0, 0, 0)
"""

import json
import os
import sys
import time
from datetime import datetime
from typing import Optional, Tuple

import requests
from PIL import Image, ImageDraw

# Allow running directly from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import get_font, get_logger

logger = get_logger("air_quality")

# ── Constants ────────────────────────────────────────────────────────────────

WIDTH = 800
HEIGHT = 480
LEGEND_W = 180          # width of left AQI legend column
LEGEND_BAR_H = 40       # height of each legend tier bar
CACHE_TTL = 3600        # 1 hour in seconds

AIRNOW_URL = (
    "https://www.airnowapi.org/aq/observation/zipCode/current/"
    "?format=application/json"
    "&zipCode={zip}"
    "&distance=25"
    "&API_KEY={key}"
)

# AQI tier definitions: (min, max_inclusive, label, rgb_color, text_color)
AQI_TIERS = [
    (0,   50,  "Good",            (0,   255,   0), (0,   0,   0)),
    (51,  100, "Moderate",        (255, 255,   0), (0,   0,   0)),
    (101, 150, "USG",             (255, 128,   0), (255, 255, 255)),
    (151, 200, "Unhealthy",       (255,   0,   0), (255, 255, 255)),
    (201, 300, "Very Unhealthy",  (150,   0, 150), (255, 255, 255)),
    (301, 999, "Hazardous",       (0,     0,   0), (255, 255, 255)),
]


# ── AQI helpers ──────────────────────────────────────────────────────────────

def _aqi_color(aqi: int) -> Tuple[int, int, int]:
    """Return the e-ink RGB color for the given AQI integer."""
    for lo, hi, _label, color, _text in AQI_TIERS:
        if aqi <= hi:
            return color
    return (0, 0, 0)  # Hazardous fallback


def _aqi_category(aqi: int) -> str:
    """Return the category name string for the given AQI integer."""
    for lo, hi, label, _color, _text in AQI_TIERS:
        if aqi <= hi:
            return label
    return "Hazardous"


# ── Cache ─────────────────────────────────────────────────────────────────────

def _cache_path(cache_dir: str) -> str:
    return os.path.join(cache_dir, "aqi_cache.json")


def _load_cache(cache_dir: str) -> Optional[dict]:
    """Load cached AQI data if it exists. Returns dict or None."""
    path = _cache_path(cache_dir)
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data
    except Exception:
        return None


def _save_cache(cache_dir: str, payload: dict) -> None:
    """Write AQI payload to cache file."""
    os.makedirs(cache_dir, exist_ok=True)
    path = _cache_path(cache_dir)
    try:
        with open(path, "w") as f:
            json.dump(payload, f)
    except Exception as e:
        logger.warning("Could not write AQI cache: %s", e)


# ── Data fetch ───────────────────────────────────────────────────────────────

def _fetch_aqi(zip_code: str, api_key: str) -> Optional[dict]:
    """
    Fetch current AQI from AirNow for the given ZIP code.
    Returns a dict with keys: aqi, category, parameter.
    Returns None on failure.
    """
    url = AIRNOW_URL.format(zip=zip_code, key=api_key)
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        entries = resp.json()
    except Exception as e:
        logger.error("AirNow fetch failed: %s", e)
        return None

    if not entries or not isinstance(entries, list):
        logger.warning("AirNow returned empty/invalid response")
        return None

    # Prefer PM2.5 entry; fall back to first available
    pm25 = next((e for e in entries if e.get("ParameterName", "").upper() == "PM2.5"), None)
    entry = pm25 or entries[0]

    aqi_val = entry.get("AQI")
    if aqi_val is None:
        logger.warning("AirNow entry missing AQI field")
        return None

    return {
        "aqi":       int(aqi_val),
        "category":  entry.get("Category", {}).get("Name", _aqi_category(int(aqi_val))),
        "parameter": entry.get("ParameterName", ""),
        "fetched_at": time.time(),
    }


def _get_aqi_data(zip_code: str, api_key: str, cache_dir: str) -> Optional[dict]:
    """
    Return AQI data, using a 1-hour cache.
    On API failure, falls back to stale cache.
    Returns None only if no data is available at all.
    """
    os.makedirs(cache_dir, exist_ok=True)
    cache = _load_cache(cache_dir)

    # Return fresh cache if still valid
    if cache and (time.time() - cache.get("fetched_at", 0)) < CACHE_TTL:
        logger.info("Using cached AQI data (age: %.0fs)", time.time() - cache["fetched_at"])
        return cache

    # Attempt live fetch
    if api_key:
        data = _fetch_aqi(zip_code, api_key)
        if data:
            _save_cache(cache_dir, data)
            logger.info("Fetched AQI %d (%s) for ZIP %s", data["aqi"], data["category"], zip_code)
            return data
        else:
            logger.warning("Live AQI fetch failed — trying stale cache")

    # Fall back to stale cache
    if cache:
        age_min = (time.time() - cache.get("fetched_at", 0)) / 60
        logger.warning("Using stale AQI cache (age: %.0f min)", age_min)
        return cache

    return None


# ── Drawing helpers ───────────────────────────────────────────────────────────

def _draw_legend(draw: ImageDraw.Draw, config: dict) -> None:
    """
    Draw the 6-tier AQI color legend in the left 180px column.
    Each bar is LEGEND_BAR_H pixels tall; the block is centered vertically.
    """
    total_h = len(AQI_TIERS) * LEGEND_BAR_H
    y0 = (HEIGHT - total_h) // 2
    bar_w = LEGEND_W - 4  # 2px margin each side

    label_font = get_font(11, bold=False, config=config)
    range_font = get_font(10, bold=False, config=config)

    for i, (lo, hi, label, bg_color, text_color) in enumerate(AQI_TIERS):
        y = y0 + i * LEGEND_BAR_H
        # Color bar
        draw.rectangle([(2, y), (2 + bar_w - 1, y + LEGEND_BAR_H - 1)], fill=bg_color)
        # Thin separator line
        draw.line([(2, y + LEGEND_BAR_H - 1), (2 + bar_w - 1, y + LEGEND_BAR_H - 1)],
                  fill=(200, 200, 200), width=1)

        # Category label (centered horizontally in bar)
        lbl_bb = draw.textbbox((0, 0), label, font=label_font)
        lbl_w = lbl_bb[2] - lbl_bb[0]
        lbl_h = lbl_bb[3] - lbl_bb[1]
        lbl_x = 2 + (bar_w - lbl_w) // 2
        lbl_y = y + (LEGEND_BAR_H - lbl_h) // 2 - 6
        draw.text((lbl_x, lbl_y), label, fill=text_color, font=label_font)

        # AQI range (small, below label)
        range_str = f"{lo}–{hi}" if hi < 999 else f"{lo}+"
        rng_bb = draw.textbbox((0, 0), range_str, font=range_font)
        rng_w = rng_bb[2] - rng_bb[0]
        rng_x = 2 + (bar_w - rng_w) // 2
        rng_y = lbl_y + lbl_h + 2
        draw.text((rng_x, rng_y), range_str, fill=text_color, font=range_font)

    # Vertical separator line between legend and content
    draw.line([(LEGEND_W, 0), (LEGEND_W, HEIGHT)], fill=(180, 180, 180), width=1)


def _fit_aqi_number(draw: ImageDraw.Draw, aqi_str: str, config: dict,
                    content_x: int, content_w: int,
                    target_h: int = 300) -> Tuple[ImageDraw.ImageDraw, object, int]:
    """
    Find the largest font size that fits aqi_str within content_w and target_h.
    Returns (font, actual_text_width).
    """
    best_font = get_font(10, bold=True, config=config)
    best_w = 0
    for size in range(target_h, 30, -4):
        font = get_font(size, bold=True, config=config)
        bb = draw.textbbox((0, 0), aqi_str, font=font)
        w = bb[2] - bb[0]
        h = bb[3] - bb[1]
        if w <= content_w and h <= target_h:
            best_font = font
            best_w = w
            break
    return best_font, best_w


def _draw_unavailable(img: Image.Image, config: dict) -> None:
    """Draw an 'AQI Unavailable' fallback message centered on the image."""
    draw = ImageDraw.Draw(img)
    font = get_font(36, bold=True, config=config)
    msg = "AQI Unavailable"
    bb = draw.textbbox((0, 0), msg, font=font)
    x = (WIDTH - (bb[2] - bb[0])) // 2
    y = (HEIGHT - (bb[3] - bb[1])) // 2
    draw.text((x, y), msg, fill=(120, 120, 120), font=font)

    sub = get_font(16, config=config)
    hint = "Check api_key and zip_code in config"
    hbb = draw.textbbox((0, 0), hint, font=sub)
    hx = (WIDTH - (hbb[2] - hbb[0])) // 2
    hy = y + (bb[3] - bb[1]) + 12
    draw.text((hx, hy), hint, fill=(160, 160, 160), font=sub)


# ── Main generate() ───────────────────────────────────────────────────────────

def generate(config: dict) -> str:
    """
    Generate the AQI display image and return the output BMP file path.

    Reads config section:
        air_quality:
          output_path: images/aqi_display.bmp
          zip_code: "37064"
          api_key: ""          # free key from airnow.gov
          cache_dir: data/
    """
    cfg = config.get("air_quality", {})
    output_path = cfg.get("output_path", "images/aqi_display.bmp")
    zip_code    = cfg.get("zip_code", "")
    api_key     = cfg.get("api_key", "").strip()
    cache_dir   = cfg.get("cache_dir", "data/")

    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
    os.makedirs(cache_dir, exist_ok=True)

    # ── Fetch data ─────────────────────────────────────────────────────────
    data = None
    if not api_key:
        logger.warning("AQI api_key not configured — showing unavailable screen")
    else:
        data = _get_aqi_data(zip_code, api_key, cache_dir)

    # ── Create canvas ──────────────────────────────────────────────────────
    img = Image.new("RGB", (WIDTH, HEIGHT), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    if data is None:
        _draw_unavailable(img, config)
        img.save(output_path)
        logger.info("Saved AQI unavailable display to %s", output_path)
        return output_path

    aqi_val    = data["aqi"]
    category   = data["category"]
    parameter  = data["parameter"]
    fetched_at = data.get("fetched_at", 0)

    aqi_color  = _aqi_color(aqi_val)
    aqi_str    = str(aqi_val)

    # ── Layout measurements ────────────────────────────────────────────────
    content_x = LEGEND_W + 10          # left edge of main content area
    content_w = WIDTH - content_x - 10 # available width for content
    GRAY      = (120, 120, 120)

    # ── Left legend ────────────────────────────────────────────────────────
    _draw_legend(draw, config)

    # ── "Air Quality Index" header label (top-center of content area) ──────
    header_font = get_font(18, config=config)
    header_text = "Air Quality Index"
    h_bb = draw.textbbox((0, 0), header_text, font=header_font)
    h_w  = h_bb[2] - h_bb[0]
    h_cx = content_x + (content_w - h_w) // 2
    draw.text((h_cx, 18), header_text, fill=GRAY, font=header_font)

    # ── Giant AQI number ──────────────────────────────────────────────────
    # Reserve space: top 40px for header, bottom ~90px for labels + footer
    top_offset   = 50
    bottom_guard = 90
    number_area_h = HEIGHT - top_offset - bottom_guard  # ~340px

    num_font, num_w = _fit_aqi_number(
        draw, aqi_str, config,
        content_x=content_x,
        content_w=content_w,
        target_h=number_area_h,
    )
    n_bb  = draw.textbbox((0, 0), aqi_str, font=num_font)
    n_w   = n_bb[2] - n_bb[0]
    n_h   = n_bb[3] - n_bb[1]
    # Center the number in the available zone
    n_cx  = content_x + (content_w - n_w) // 2
    n_cy  = top_offset + (number_area_h - n_h) // 2

    draw.text((n_cx, n_cy), aqi_str, fill=aqi_color, font=num_font)

    # ── Category name ──────────────────────────────────────────────────────
    cat_font  = get_font(40, bold=True, config=config)
    cat_bb    = draw.textbbox((0, 0), category, font=cat_font)
    cat_w     = cat_bb[2] - cat_bb[0]
    cat_h     = cat_bb[3] - cat_bb[1]
    cat_cx    = content_x + (content_w - cat_w) // 2
    cat_cy    = n_cy + n_h + 6
    draw.text((cat_cx, cat_cy), category, fill=aqi_color, font=cat_font)

    # ── Parameter name (PM2.5 / Ozone) ────────────────────────────────────
    if parameter:
        param_font = get_font(18, config=config)
        param_bb   = draw.textbbox((0, 0), parameter, font=param_font)
        param_w    = param_bb[2] - param_bb[0]
        param_cx   = content_x + (content_w - param_w) // 2
        param_cy   = cat_cy + cat_h + 6
        draw.text((param_cx, param_cy), parameter, fill=GRAY, font=param_font)

    # ── Bottom-right footer: ZIP + updated time ────────────────────────────
    if fetched_at:
        ts = datetime.fromtimestamp(fetched_at).strftime("%-I:%M %p")
    else:
        ts = datetime.now().strftime("%-I:%M %p")
    footer_text = f"{zip_code}  Updated {ts}"
    footer_font = get_font(13, config=config)
    f_bb  = draw.textbbox((0, 0), footer_text, font=footer_font)
    f_w   = f_bb[2] - f_bb[0]
    f_h   = f_bb[3] - f_bb[1]
    draw.text((WIDTH - f_w - 8, HEIGHT - f_h - 6), footer_text, fill=GRAY, font=footer_font)

    # ── Save ───────────────────────────────────────────────────────────────
    img.save(output_path)
    logger.info("Saved AQI display (AQI=%d, %s) to %s", aqi_val, category, output_path)
    return output_path


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import yaml

    config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yml")
    with open(config_path, "r") as f:
        cfg = yaml.safe_load(f)

    result = generate(cfg)
    print(f"Generated: {result}")
