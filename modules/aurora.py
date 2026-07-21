"""
Aurora (Northern Lights) Forecast Module — E-ink display showing the current
planetary Kp index and a latitude-aware visibility verdict, sourced from the
keyless NOAA SWPC space-weather feeds.

800×480 pixels, output as BMP.

Config section (add to config.yml):

    aurora:
      output_path: images/aurora_display.bmp
      cache_dir: data/

Reads the shared forecast_location block for latitude/longitude/name:

    forecast_location:
      latitude: 35.9251
      longitude: -86.8689
      name: "Franklin, TN"

Data sources (no API key required):
    Current Kp:  https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json
    3-day fcst:  https://services.swpc.noaa.gov/text/3-day-forecast.txt

Kp color scale (e-ink colors):
    0–2  Green  (0,   255,   0)
    3–4  Yellow (255, 255,   0)
    5–6  Orange (255, 128,   0)
    7–9  Red    (255,   0,   0)
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from typing import List, Optional, Tuple

import requests
from PIL import Image, ImageDraw

# Allow running directly from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import get_font, get_logger

logger = get_logger("aurora")

# ── Constants ────────────────────────────────────────────────────────────────

WIDTH = 800
HEIGHT = 480
CACHE_TTL = 1800        # 30 minutes in seconds

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
GRAY = (120, 120, 120)

GREEN = (0, 255, 0)
YELLOW = (255, 255, 0)
ORANGE = (255, 128, 0)
RED = (255, 0, 0)

KP_JSON_URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
FORECAST_TXT_URL = "https://services.swpc.noaa.gov/text/3-day-forecast.txt"

# Visibility thresholds: (min |latitude|, minimum Kp for possible visibility).
# Ordered high→low latitude; first match wins.
_LAT_THRESHOLDS = [
    (66, 1),
    (60, 2),
    (55, 3),
    (52, 4),
    (48, 5),
    (45, 6),
    (43, 7),
    (40, 8),
]


# ── Kp helpers ───────────────────────────────────────────────────────────────

def _kp_color(kp: float) -> Tuple[int, int, int]:
    """Return the e-ink RGB color for the given Kp value."""
    if kp <= 2:
        return GREEN
    if kp <= 4:
        return YELLOW
    if kp <= 6:
        return ORANGE
    return RED


def _kp_threshold(latitude: float) -> int:
    """Return the minimum Kp for possible aurora visibility at this latitude."""
    lat = abs(latitude)
    for min_lat, kp in _LAT_THRESHOLDS:
        if lat >= min_lat:
            return kp
    return 9


# ── Cache ────────────────────────────────────────────────────────────────────

def _cache_path(cache_dir: str) -> str:
    return os.path.join(cache_dir, "aurora_cache.json")


def _load_cache(cache_dir: str) -> Optional[dict]:
    try:
        with open(_cache_path(cache_dir), "r") as f:
            return json.load(f)
    except Exception:
        return None


def _save_cache(cache_dir: str, payload: dict) -> None:
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(_cache_path(cache_dir), "w") as f:
            json.dump(payload, f)
    except Exception as exc:
        logger.warning("Failed to write aurora cache: %s", exc)


# ── Fetch ────────────────────────────────────────────────────────────────────

def _fetch_current_kp() -> Optional[float]:
    """Fetch the latest planetary Kp index (0–9) from NOAA SWPC."""
    try:
        resp = requests.get(KP_JSON_URL, timeout=15)
        resp.raise_for_status()
        rows = resp.json()
    except Exception as exc:
        logger.warning("Failed to fetch current Kp: %s", exc)
        return None

    if not isinstance(rows, list) or len(rows) < 2:
        logger.warning("Unexpected Kp JSON structure")
        return None

    # Take the last data row. NOAA has served two shapes for this feed:
    #   • list-of-lists with a header row first → Kp is column index 1
    #   • list-of-dicts keyed by name          → Kp is the "Kp" key
    last = rows[-1]
    try:
        if isinstance(last, dict):
            return float(last["Kp"])
        return float(last[1])
    except (IndexError, KeyError, ValueError, TypeError) as exc:
        logger.warning("Could not parse Kp value: %s", exc)
        return None


def _fetch_forecast() -> Optional[List[dict]]:
    """Parse the NOAA 3-day forecast text into max-Kp-per-day entries.

    Returns a list of up to three dicts: {"day": "Jul 21", "kp": 5.0}.
    Returns None if parsing is not possible (caller degrades gracefully).
    """
    try:
        resp = requests.get(FORECAST_TXT_URL, timeout=15)
        resp.raise_for_status()
        text = resp.text
    except Exception as exc:
        logger.warning("Failed to fetch 3-day forecast: %s", exc)
        return None

    try:
        return _parse_forecast_text(text)
    except Exception as exc:
        logger.warning("Failed to parse 3-day forecast: %s", exc)
        return None


def _parse_forecast_text(text: str) -> Optional[List[dict]]:
    """Extract max forecast Kp per day from the SWPC breakdown table.

    The table looks like:

        NOAA Kp index breakdown Jul 21-Jul 23

                      Jul 21       Jul 22       Jul 23
        00-03UT        3.00         2.67         2.33
        03-06UT        2.67         2.33 (G1)    4.00
        ...
    """
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if "NOAA Kp index breakdown" in line:
            start = i
            break
    if start is None:
        return None

    # Find the header row with the three day labels (e.g. "Jul 21  Jul 22 ...").
    day_labels: List[str] = []
    header_idx = None
    month_re = re.compile(
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}"
    )
    for i in range(start + 1, min(start + 6, len(lines))):
        found = month_re.findall(lines[i])
        # month_re.findall returns the month group only; re-scan for full labels.
        matches = re.findall(
            r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}",
            lines[i],
        )
        if len(matches) >= 3:
            day_labels = matches[:3]
            header_idx = i
            break
    if header_idx is None or not day_labels:
        return None

    max_kp = [0.0, 0.0, 0.0]
    row_re = re.compile(r"^\s*\d{2}-\d{2}UT")
    for line in lines[header_idx + 1:]:
        if not row_re.match(line):
            # Stop once we leave the time-block rows.
            if line.strip() and not line.startswith(" "):
                break
            continue
        # Grab the first three float values in the row.
        floats = re.findall(r"\d+\.\d+", line)
        for col in range(min(3, len(floats))):
            try:
                val = float(floats[col])
            except ValueError:
                continue
            if val > max_kp[col]:
                max_kp[col] = val

    if all(v == 0.0 for v in max_kp):
        return None

    return [
        {"day": day_labels[i], "kp": max_kp[i]}
        for i in range(3)
    ]


def _get_data(cache_dir: str) -> Optional[dict]:
    """Return aurora data, using a fresh fetch, then falling back to cache.

    Payload: {"current_kp": float, "forecast": [...]|None, "fetched_at": ts}
    """
    cache = _load_cache(cache_dir)
    now = time.time()

    if cache and (now - cache.get("fetched_at", 0)) < CACHE_TTL:
        logger.info("Using cached aurora data")
        return cache

    current_kp = _fetch_current_kp()
    if current_kp is not None:
        forecast = _fetch_forecast()
        payload = {
            "current_kp": current_kp,
            "forecast": forecast,
            "fetched_at": now,
        }
        _save_cache(cache_dir, payload)
        return payload

    if cache:
        logger.warning("Fetch failed; falling back to stale aurora cache")
        return cache

    return None


# ── Drawing helpers ──────────────────────────────────────────────────────────

def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _center_text(draw, text, y, font, fill, config):
    w = _text_w(draw, text, font)
    draw.text(((WIDTH - w) / 2, y), text, font=font, fill=fill)


def _fit_font(draw, text, max_w, start_size, config, bold=True, min_size=14):
    """Return the largest font whose rendered width fits within max_w."""
    size = start_size
    while size > min_size:
        font = get_font(size, bold=bold, config=config)
        if _text_w(draw, text, font) <= max_w:
            return font
        size -= 2
    return get_font(min_size, bold=bold, config=config)


def _draw_gauge(draw, cx_left, cx_right, y, height, kp, config):
    """Draw a horizontal Kp gauge (0→9) with colored segments and a marker."""
    span = cx_right - cx_left
    seg_w = span / 9.0

    # Segment colors by Kp bucket (index 0..8 → Kp 1..9 region).
    for i in range(9):
        x0 = cx_left + i * seg_w
        x1 = cx_left + (i + 1) * seg_w
        # Color reflects the Kp value at the segment's midpoint.
        color = _kp_color(i + 0.5)
        draw.rectangle([x0, y, x1, y + height], fill=color)

    # Border and tick labels.
    draw.rectangle([cx_left, y, cx_right, y + height], outline=BLACK, width=2)
    tick_font = get_font(18, bold=False, config=config)
    for i in range(10):
        x = cx_left + i * seg_w
        draw.line([x, y + height, x, y + height + 6], fill=BLACK, width=1)
        label = str(i)
        lw = _text_w(draw, label, tick_font)
        draw.text((x - lw / 2, y + height + 8), label, font=tick_font, fill=GRAY)

    # Marker triangle at the current Kp position.
    kp_clamped = max(0.0, min(9.0, kp))
    mx = cx_left + kp_clamped * seg_w
    draw.polygon(
        [(mx, y - 4), (mx - 12, y - 24), (mx + 12, y - 24)],
        fill=BLACK,
    )
    draw.rectangle([mx - 2, y - 4, mx + 2, y + height + 4], fill=BLACK)


def _draw_unavailable(img, config):
    draw = ImageDraw.Draw(img)
    font = get_font(48, bold=True, config=config)
    _center_text(draw, "Aurora data unavailable", HEIGHT / 2 - 30, font, BLACK, config)
    sub = get_font(22, bold=False, config=config)
    _center_text(draw, "NOAA SWPC — no data or cache", HEIGHT / 2 + 30, sub, GRAY, config)


# ── Main entry point ─────────────────────────────────────────────────────────

def generate(config: dict) -> str:
    """
    Generate the aurora forecast display image and return the output BMP path.

    Config section:
        aurora:
          output_path: images/aurora_display.bmp
          cache_dir: data/
    """
    cfg = config.get("aurora", {})
    loc = config.get("forecast_location", {})

    output_path = cfg.get("output_path", "images/aurora_display.bmp")
    cache_dir = cfg.get("cache_dir", "data/")

    latitude = float(loc.get("latitude", 0.0))
    loc_name = loc.get("name", "Unknown location")

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    img = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(img)

    data = _get_data(cache_dir)

    if data is None:
        _draw_unavailable(img, config)
        img.save(output_path)
        logger.info("Saved aurora unavailable display to %s", output_path)
        return output_path

    kp = float(data["current_kp"])
    forecast = data.get("forecast")
    fetched_at = data.get("fetched_at", 0)

    kp_color = _kp_color(kp)
    threshold = _kp_threshold(latitude)
    visible = kp >= threshold

    # ── Title ────────────────────────────────────────────────────────────────
    title_font = get_font(46, bold=True, config=config)
    _center_text(draw, "Aurora Forecast", 18, title_font, BLACK, config)

    # ── Giant Kp number ──────────────────────────────────────────────────────
    # Format: whole number if integer-ish, else one decimal.
    kp_str = f"{kp:.0f}" if abs(kp - round(kp)) < 0.05 else f"{kp:.1f}"
    big_font = get_font(190, bold=True, config=config)
    big_w = _text_w(draw, kp_str, big_font)
    big_bbox = draw.textbbox((0, 0), kp_str, font=big_font)
    big_h = big_bbox[3] - big_bbox[1]
    big_x = (WIDTH - big_w) / 2
    big_y = 70
    draw.text((big_x, big_y - big_bbox[1]), kp_str, font=big_font, fill=kp_color)

    # "Kp" label to the right of the giant number.
    label_font = get_font(56, bold=True, config=config)
    draw.text(
        (big_x + big_w + 16, big_y + big_h - 60),
        "Kp",
        font=label_font,
        fill=GRAY,
    )

    # ── Kp gauge ─────────────────────────────────────────────────────────────
    gauge_left = 120
    gauge_right = WIDTH - 120
    gauge_y = 288
    gauge_h = 32
    _draw_gauge(draw, gauge_left, gauge_right, gauge_y, gauge_h, kp, config)

    # ── Verdict ──────────────────────────────────────────────────────────────
    verdict = (
        "Aurora may be visible tonight"
        if visible
        else "Not visible from your latitude"
    )
    verdict_color = kp_color if visible else GRAY
    verdict_font = _fit_font(draw, verdict, WIDTH - 60, 40, config, bold=True)
    _center_text(draw, verdict, 348, verdict_font, verdict_color, config)

    # ── 3-day forecast columns ───────────────────────────────────────────────
    if forecast:
        col_font = get_font(20, bold=False, config=config)
        col_kp_font = get_font(30, bold=True, config=config)
        n = len(forecast)
        row_y = 398
        # Evenly space the columns across the width.
        band_left = 80
        band_right = WIDTH - 80
        band_span = band_right - band_left
        for i, day in enumerate(forecast):
            cx = band_left + band_span * (i + 0.5) / n
            d_kp = float(day.get("kp", 0.0))
            d_color = _kp_color(d_kp)
            d_label = day.get("day", "")
            d_kp_str = f"{d_kp:.0f}" if abs(d_kp - round(d_kp)) < 0.05 else f"{d_kp:.1f}"

            lw = _text_w(draw, d_label, col_font)
            draw.text((cx - lw / 2, row_y), d_label, font=col_font, fill=GRAY)

            kw = _text_w(draw, d_kp_str, col_kp_font)
            draw.text((cx - kw / 2, row_y + 22), d_kp_str, font=col_kp_font, fill=BLACK)

            # Color dot to the right of the Kp value.
            dot_r = 9
            dot_x = cx + kw / 2 + 14
            dot_y = row_y + 38
            draw.ellipse(
                [dot_x - dot_r, dot_y - dot_r, dot_x + dot_r, dot_y + dot_r],
                fill=d_color,
                outline=BLACK,
            )

    # ── Footer ───────────────────────────────────────────────────────────────
    footer_font = get_font(18, bold=False, config=config)
    if fetched_at:
        upd = datetime.fromtimestamp(fetched_at).strftime("%b %d %I:%M %p")
    else:
        upd = "unknown"
    footer = f"{loc_name}  •  NOAA SWPC  •  Updated {upd}"
    fw = _text_w(draw, footer, footer_font)
    draw.text(((WIDTH - fw) / 2, HEIGHT - 26), footer, font=footer_font, fill=GRAY)

    img.save(output_path)
    logger.info(
        "Saved aurora display (Kp=%.2f, threshold=%d, visible=%s) to %s",
        kp, threshold, visible, output_path,
    )
    return output_path


if __name__ == "__main__":
    import yaml

    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yml"
    )
    with open(config_path, "r") as f:
        cfg = yaml.safe_load(f)

    result = generate(cfg)
    print(f"Generated: {result}")
