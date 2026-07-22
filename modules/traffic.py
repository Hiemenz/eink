"""
Local Traffic Incidents Display Module — E-ink display showing current
traffic incidents (accidents, closures, jams, roadworks) within a radius of
the forecast location, sourced from the TomTom Traffic Incidents API.

800×480 pixels, output as BMP.

Config section (add to config.yml):

    traffic:
      output_path: images/traffic_display.bmp
      api_key: ""          # free key from developer.tomtom.com
      radius_deg: 0.15     # bbox half-size around forecast_location
      cache_dir: data/

Shared config used:

    forecast_location:
      latitude: 35.9251
      longitude: -86.8689
      name: "Franklin, TN"

Data source (requires a free API key):
    https://api.tomtom.com/traffic/services/5/incidentDetails
        ?key={key}
        &bbox={minLon},{minLat},{maxLon},{maxLat}
        &fields={fields}
        &language=en-US
        &categoryFilter=0,1,2,3,4,5,6,7,8,9,10,11
        &timeValidityFilter=present

Each incident carries:
    properties.events[0].description   — human-readable summary
    properties.magnitudeOfDelay        — 0..4 (higher = worse)
    properties.delay                   — delay in seconds
    properties.iconCategory            — integer category code

Magnitude → severity mapping (e-ink colors):
    0  Unknown  → Gray   (120, 120, 120)
    1  Minor    → Green  (0,   200,   0)
    2  Moderate → Yellow (245, 200,   0)
    3  Major    → Orange (255, 128,   0)
    4  Severe   → Red    (220,   0,   0)

Cache: 5 minutes in data/traffic_cache.json; stale cache used on failure.
"""

import json
import os
import sys
import time
from datetime import datetime
from typing import List, Optional

import requests
from PIL import Image, ImageDraw

# Allow running directly from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import get_font, get_logger

logger = get_logger("traffic")

# ── Constants ────────────────────────────────────────────────────────────────

WIDTH = 800
HEIGHT = 480
CACHE_TTL = 300  # 5 minutes in seconds
DEFAULT_RADIUS_DEG = 0.15
MAX_ROWS = 8

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
GRAY = (120, 120, 120)
LIGHT_GRAY = (190, 190, 190)

# Severity tiers by magnitudeOfDelay: {mag: (label, rgb, text_on_color)}
SEVERITY = {
    0: ("Unknown",  GRAY,            WHITE),
    1: ("Minor",    (0,   200,   0), BLACK),
    2: ("Moderate", (245, 200,   0), BLACK),
    3: ("Major",    (255, 128,   0), BLACK),
    4: ("Severe",   (220,   0,   0), WHITE),
}

API_URL = "https://api.tomtom.com/traffic/services/5/incidentDetails"

# Requested fields (URL-encoding is handled by requests via the params dict).
FIELDS = (
    "{incidents{type,geometry{type,coordinates},"
    "properties{iconCategory,magnitudeOfDelay,"
    "events{description,code},startTime,endTime,delay,length}}}"
)


# ── Cache ─────────────────────────────────────────────────────────────────────

def _cache_path(cache_dir: str) -> str:
    return os.path.join(cache_dir, "traffic_cache.json")


def _load_cache(cache_dir: str) -> Optional[dict]:
    """Load cached traffic data if present. Returns dict or None."""
    try:
        with open(_cache_path(cache_dir), "r") as f:
            return json.load(f)
    except Exception:
        return None


def _save_cache(cache_dir: str, payload: dict) -> None:
    """Write traffic payload to cache file."""
    os.makedirs(cache_dir, exist_ok=True)
    try:
        with open(_cache_path(cache_dir), "w") as f:
            json.dump(payload, f)
    except Exception as e:
        logger.warning("Could not write traffic cache: %s", e)


# ── Data fetch ───────────────────────────────────────────────────────────────

def _fetch_incidents(key: str, lat: float, lon: float, radius_deg: float) -> Optional[dict]:
    """Fetch current traffic incidents from TomTom for a bbox around (lat, lon).

    Returns a normalized dict {"incidents": [...], "fetched_at": ts} or None
    on failure.
    """
    min_lon = lon - radius_deg
    min_lat = lat - radius_deg
    max_lon = lon + radius_deg
    max_lat = lat + radius_deg
    bbox = f"{min_lon},{min_lat},{max_lon},{max_lat}"

    params = {
        "key": key,
        "bbox": bbox,
        "fields": FIELDS,
        "language": "en-US",
        "categoryFilter": "0,1,2,3,4,5,6,7,8,9,10,11",
        "timeValidityFilter": "present",
    }

    try:
        resp = requests.get(API_URL, params=params, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        logger.error("TomTom traffic fetch failed: %s", e)
        return None

    incidents = payload.get("incidents", []) or []
    parsed: List[dict] = []
    for inc in incidents:
        props = inc.get("properties", {}) or {}
        events = props.get("events", []) or []
        description = ""
        if events:
            description = (events[0] or {}).get("description", "") or ""
        parsed.append({
            "description": description,
            "magnitude": props.get("magnitudeOfDelay", 0) or 0,
            "delay": props.get("delay", 0) or 0,
            "icon_category": props.get("iconCategory", 0) or 0,
        })

    return {"incidents": parsed, "fetched_at": time.time()}


def _get_traffic_data(key: str, lat: float, lon: float, radius_deg: float,
                      cache_dir: str) -> Optional[dict]:
    """Return traffic data using a 5-minute cache; stale cache on failure.

    Returns None only when the HTTP request fails AND no cache exists.
    """
    os.makedirs(cache_dir, exist_ok=True)
    cache = _load_cache(cache_dir)

    # Fresh cache still valid
    if cache and (time.time() - cache.get("fetched_at", 0)) < CACHE_TTL:
        logger.info("Using cached traffic data (age: %.0fs)",
                    time.time() - cache["fetched_at"])
        return cache

    # Live fetch
    data = _fetch_incidents(key, lat, lon, radius_deg)
    if data:
        _save_cache(cache_dir, data)
        logger.info("Fetched %d traffic incidents for (%.4f, %.4f)",
                    len(data["incidents"]), lat, lon)
        return data

    logger.warning("Live traffic fetch failed — trying stale cache")
    if cache:
        age_min = (time.time() - cache.get("fetched_at", 0)) / 60
        logger.warning("Using stale traffic cache (age: %.0f min)", age_min)
        return cache

    return None


# ── Drawing helpers ───────────────────────────────────────────────────────────

def _center_text(draw, cx, y, text, font, fill):
    """Draw text horizontally centered on cx at vertical position y."""
    bb = draw.textbbox((0, 0), text, font=font)
    draw.text((cx - (bb[2] - bb[0]) // 2, y), text, fill=fill, font=font)


def _truncate(draw, text, font, max_w) -> str:
    """Truncate text with an ellipsis so it fits within max_w pixels."""
    if not text:
        return ""
    if draw.textlength(text, font=font) <= max_w:
        return text
    ell = "…"
    while text and draw.textlength(text + ell, font=font) > max_w:
        text = text[:-1]
    return (text.rstrip() + ell) if text else ell


def _severity_for(mag: int) -> tuple:
    """Return the severity tuple for a magnitudeOfDelay value."""
    return SEVERITY.get(mag if mag in SEVERITY else 0, SEVERITY[0])


def _fmt_delay(delay_seconds) -> str:
    """Format a delay in seconds as whole minutes (e.g. '5 min')."""
    try:
        secs = float(delay_seconds)
    except (TypeError, ValueError):
        return ""
    if secs <= 0:
        return ""
    minutes = int(round(secs / 60.0))
    if minutes < 1:
        minutes = 1
    return f"{minutes} min"


def _count_color(count: int) -> tuple:
    """Color the incident-count stat: green (0), yellow (few), red (many)."""
    if count == 0:
        return (0, 200, 0)
    if count <= 4:
        return (245, 200, 0)
    if count <= 8:
        return (255, 128, 0)
    return (220, 0, 0)


def _draw_header(draw, area_name, config):
    """Draw the full-width black header bar with title, area, and time."""
    bar_h = 56
    draw.rectangle([(0, 0), (WIDTH, bar_h)], fill=BLACK)

    title_font = get_font(30, bold=True, config=config)
    draw.text((20, (bar_h - 30) // 2 - 2), "Traffic", fill=WHITE, font=title_font)

    # Time, right-aligned
    time_font = get_font(22, config=config)
    time_str = datetime.now().strftime("%-I:%M %p")
    tbb = draw.textbbox((0, 0), time_str, font=time_font)
    draw.text((WIDTH - (tbb[2] - tbb[0]) - 20, (bar_h - (tbb[3] - tbb[1])) // 2 - tbb[1]),
              time_str, fill=WHITE, font=time_font)

    # Area name, centered
    if area_name:
        area_font = get_font(20, config=config)
        _center_text(draw, WIDTH // 2, (bar_h - 20) // 2 - 1, area_name, area_font, WHITE)
    return bar_h


def _draw_summary(draw, top, count, total_delay_min, config):
    """Draw the big summary stats: incident count + total delay minutes."""
    # Incident count block (left)
    count_color = _count_color(count)
    num_font = get_font(72, bold=True, config=config)
    lbl_font = get_font(16, config=config)

    cx_count = 150
    _center_text(draw, cx_count, top + 4, str(count), num_font, count_color)
    _center_text(draw, cx_count, top + 88, "INCIDENTS", lbl_font, GRAY)

    # Divider
    draw.line([(300, top + 10), (300, top + 100)], fill=LIGHT_GRAY, width=2)

    # Total delay block (right)
    cx_delay = 500
    delay_str = f"{total_delay_min}" if total_delay_min else "0"
    delay_color = BLACK if total_delay_min == 0 else _count_color(max(count, 1))
    _center_text(draw, cx_delay, top + 4, delay_str, num_font, delay_color)
    _center_text(draw, cx_delay, top + 88, "TOTAL DELAY (MIN)", lbl_font, GRAY)

    # Severity legend (far right)
    _draw_legend(draw, 640, top + 12, config)


def _draw_legend(draw, x, y, config):
    """Draw a compact vertical severity legend."""
    font = get_font(13, config=config)
    dot_r = 6
    row_h = 19
    for i in range(1, 5):
        label, color, _txt = SEVERITY[i]
        cy = y + (i - 1) * row_h + dot_r
        draw.ellipse([(x, cy - dot_r), (x + dot_r * 2, cy + dot_r)],
                     fill=color, outline=LIGHT_GRAY, width=1)
        draw.text((x + dot_r * 2 + 8, cy - 8), label, fill=BLACK, font=font)


def _draw_incident_rows(draw, top, bottom, incidents, config):
    """Draw up to MAX_ROWS incident rows sorted by magnitude descending."""
    ordered = sorted(incidents, key=lambda i: i.get("magnitude", 0), reverse=True)
    ordered = ordered[:MAX_ROWS]

    if not ordered:
        font = get_font(24, config=config)
        _center_text(draw, WIDTH // 2, (top + bottom) // 2 - 16,
                     "No traffic incidents reported", font, (0, 160, 0))
        return

    margin_x = 24
    row_h = (bottom - top) // MAX_ROWS
    desc_font = get_font(19, config=config)
    delay_font = get_font(19, bold=True, config=config)

    dot_r = 7
    dot_x = margin_x
    text_x = dot_x + dot_r * 2 + 16
    delay_area_w = 90
    text_max_w = WIDTH - margin_x - delay_area_w - text_x

    for i, inc in enumerate(ordered):
        ry = top + i * row_h
        mid = ry + row_h // 2
        mag = inc.get("magnitude", 0)
        _label, color, _txt = _severity_for(mag)

        # Severity dot
        draw.ellipse([(dot_x, mid - dot_r), (dot_x + dot_r * 2, mid + dot_r)],
                     fill=color, outline=LIGHT_GRAY, width=1)

        # Description (truncated)
        desc = inc.get("description") or "Traffic incident"
        desc = _truncate(draw, desc, desc_font, text_max_w)
        dbb = draw.textbbox((0, 0), desc, font=desc_font)
        draw.text((text_x, mid - (dbb[3] - dbb[1]) // 2 - dbb[1]),
                  desc, fill=BLACK, font=desc_font)

        # Delay (right-aligned)
        delay_str = _fmt_delay(inc.get("delay", 0))
        if delay_str:
            pbb = draw.textbbox((0, 0), delay_str, font=delay_font)
            draw.text((WIDTH - margin_x - (pbb[2] - pbb[0]),
                       mid - (pbb[3] - pbb[1]) // 2 - pbb[1]),
                      delay_str, fill=color if mag >= 2 else GRAY, font=delay_font)

        # Row separator
        if i < len(ordered) - 1:
            draw.line([(margin_x, ry + row_h), (WIDTH - margin_x, ry + row_h)],
                      fill=(235, 235, 235), width=1)


def _draw_footer(draw, fetched_at, config):
    """Draw the gray footer: attribution + update time."""
    if fetched_at:
        ts = datetime.fromtimestamp(fetched_at).strftime("%-I:%M %p")
    else:
        ts = datetime.now().strftime("%-I:%M %p")
    footer = f"TomTom · Updated {ts}"
    font = get_font(13, config=config)
    fbb = draw.textbbox((0, 0), footer, font=font)
    draw.text((WIDTH - (fbb[2] - fbb[0]) - 10, HEIGHT - (fbb[3] - fbb[1]) - 8),
              footer, fill=GRAY, font=font)


def _draw_no_key(img, config):
    """Draw the no-config state prompting the user to set an API key."""
    draw = ImageDraw.Draw(img)
    _draw_header(draw, "", config)

    icon_font = get_font(90, bold=True, config=config)
    _center_text(draw, WIDTH // 2, 120, "TomTom", icon_font, LIGHT_GRAY)

    title_font = get_font(34, bold=True, config=config)
    _center_text(draw, WIDTH // 2, 250, "Configure TomTom API key", title_font, BLACK)

    sub_font = get_font(18, config=config)
    _center_text(draw, WIDTH // 2, 305,
                 "Set traffic.api_key in config.yml", sub_font, GRAY)
    _center_text(draw, WIDTH // 2, 335,
                 "Get a free key at developer.tomtom.com", sub_font, GRAY)


def _draw_failure(img, config):
    """Draw the hard-failure screen (HTTP failed and no cache)."""
    draw = ImageDraw.Draw(img)
    _draw_header(draw, "", config)

    font = get_font(38, bold=True, config=config)
    msg = "Traffic data unavailable"
    bb = draw.textbbox((0, 0), msg, font=font)
    x = (WIDTH - (bb[2] - bb[0])) // 2
    y = (HEIGHT - (bb[3] - bb[1])) // 2
    draw.text((x, y), msg, fill=GRAY, font=font)


# ── Main generate() ───────────────────────────────────────────────────────────

def generate(config: dict) -> str:
    """Generate the traffic display image and return the output BMP path.

    Reads config section:
        traffic:
          output_path: images/traffic_display.bmp
          api_key: ""
          radius_deg: 0.15
          cache_dir: data/
    """
    cfg = config.get("traffic", {})
    output_path = cfg.get("output_path", "images/traffic_display.bmp")
    api_key = (cfg.get("api_key") or "").strip()
    radius_deg = cfg.get("radius_deg", DEFAULT_RADIUS_DEG)
    cache_dir = cfg.get("cache_dir", "data/")

    loc = config.get("forecast_location", {})
    lat = loc.get("latitude", 35.9251)
    lon = loc.get("longitude", -86.8689)
    area_name = loc.get("name", "")

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    img = Image.new("RGB", (WIDTH, HEIGHT), WHITE)

    # No-config state: no API key set.
    if not api_key:
        logger.info("No TomTom API key configured — rendering config prompt")
        _draw_no_key(img, config)
        img.save(output_path)
        logger.info("Saved traffic config-prompt screen to %s", output_path)
        return output_path

    data = _get_traffic_data(api_key, lat, lon, radius_deg, cache_dir)

    # Hard failure: request failed AND no cache.
    if data is None:
        _draw_failure(img, config)
        img.save(output_path)
        logger.info("Saved traffic failure screen to %s", output_path)
        return output_path

    incidents = data.get("incidents", []) or []
    count = len(incidents)
    total_delay_sec = sum((i.get("delay") or 0) for i in incidents)
    total_delay_min = int(round(total_delay_sec / 60.0))
    fetched_at = data.get("fetched_at", 0)

    draw = ImageDraw.Draw(img)
    bar_h = _draw_header(draw, area_name, config)

    _draw_summary(draw, bar_h + 16, count, total_delay_min, config)

    rows_top = bar_h + 16 + 120
    rows_bottom = HEIGHT - 28
    _draw_incident_rows(draw, rows_top, rows_bottom, incidents, config)

    _draw_footer(draw, fetched_at, config)

    img.save(output_path)
    logger.info("Saved traffic display (count=%d, delay=%d min) to %s",
                count, total_delay_min, output_path)
    return output_path


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import yaml

    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yml"
    )
    with open(config_path, "r") as f:
        cfg = yaml.safe_load(f)

    result = generate(cfg)
    print(f"Generated: {result}")
