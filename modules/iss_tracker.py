"""
ISS Tracker Module — live International Space Station position on an
equirectangular world map, rendered for a Waveshare e-ink display.

800×480 pixels, output as BMP.

Config section (add to config.yml):

    iss_tracker:
      output_path: images/iss_display.bmp
      cache_dir: data/

Reads the shared forecast_location block for the observer's latitude/longitude:

    forecast_location:
      latitude: 35.9251
      longitude: -86.8689
      name: "Franklin, TN"

Data source (no API key required):
    Current ISS position:  https://api.wheretheiss.at/v1/satellites/25544
    Returns JSON with: latitude, longitude, altitude (km), velocity (km/h),
    visibility ("daylight"/"eclipsed"), timestamp.

Position is cached for 60s in data/iss_cache.json; a stale cache is used
when the live fetch fails.
"""

import json
import math
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

logger = get_logger("iss_tracker")

# ── Constants ────────────────────────────────────────────────────────────────

WIDTH = 800
HEIGHT = 480

MAP_W = 560                 # left map region width
PANEL_X = MAP_W             # info panel starts here
PANEL_W = WIDTH - MAP_W     # ~240px

CACHE_TTL = 60              # seconds
ISS_URL = "https://api.wheretheiss.at/v1/satellites/25544"
REQUEST_TIMEOUT = 10

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
GRAY = (120, 120, 120)
LIGHT_GRAY = (200, 200, 200)
RED = (220, 30, 30)
BLUE = (30, 90, 200)
PANEL_BG = (245, 245, 245)

EARTH_RADIUS_KM = 6371.0


# ── Data fetch + caching ─────────────────────────────────────────────────────

def _cache_path(cache_dir: str) -> str:
    return os.path.join(cache_dir, "iss_cache.json")


def _load_cache(cache_dir: str) -> Optional[dict]:
    path = _cache_path(cache_dir)
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return None


def _save_cache(cache_dir: str, payload: dict) -> None:
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(_cache_path(cache_dir), "w") as f:
            json.dump(payload, f)
    except Exception as exc:
        logger.warning("Failed to write ISS cache: %s", exc)


def _fetch_iss() -> dict:
    resp = requests.get(ISS_URL, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return {
        "latitude": float(data["latitude"]),
        "longitude": float(data["longitude"]),
        "altitude": float(data["altitude"]),
        "velocity": float(data["velocity"]),
        "visibility": str(data.get("visibility", "")),
        "timestamp": int(data.get("timestamp", time.time())),
        "fetched_at": int(time.time()),
    }


def _get_data(cache_dir: str) -> Optional[dict]:
    """Return ISS position, using a fresh cache when available.

    Falls back to a stale cache if the live fetch fails.
    """
    cache = _load_cache(cache_dir)
    now = time.time()

    if cache and (now - cache.get("fetched_at", 0)) < CACHE_TTL:
        return cache

    try:
        payload = _fetch_iss()
        _save_cache(cache_dir, payload)
        return payload
    except Exception as exc:
        logger.warning("ISS fetch failed (%s); using stale cache", exc)
        if cache:
            cache["stale"] = True
            return cache

    return None


# ── Geometry (stdlib math only) ──────────────────────────────────────────────

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lat/lon points."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_KM * c


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial compass bearing (0–360°) from point 1 toward point 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    x = math.sin(dlambda) * math.cos(p2)
    y = (math.cos(p1) * math.sin(p2)
         - math.sin(p1) * math.cos(p2) * math.cos(dlambda))
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def _compass_point(bearing: float) -> str:
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = int((bearing + 22.5) % 360 // 45)
    return dirs[idx]


def _lonlat_to_xy(lon: float, lat: float,
                  x0: int, y0: int, w: int, h: int) -> Tuple[int, int]:
    """Equirectangular projection into a map rectangle."""
    x = x0 + (lon + 180.0) / 360.0 * w
    y = y0 + (90.0 - lat) / 180.0 * h
    return int(round(x)), int(round(y))


# ── Drawing helpers ──────────────────────────────────────────────────────────

def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _draw_unavailable(img: Image.Image, config: dict) -> None:
    draw = ImageDraw.Draw(img)
    font = get_font(40, bold=True, config=config)
    msg = "ISS position unavailable"
    w = _text_w(draw, msg, font)
    draw.text(((WIDTH - w) // 2, HEIGHT // 2 - 20), msg, font=font, fill=BLACK)


def _draw_map(draw: ImageDraw.ImageDraw, config: dict,
              iss_lon: float, iss_lat: float,
              obs_lon: float, obs_lat: float) -> None:
    # Map rectangle inset within the left region.
    margin = 16
    x0 = margin
    y0 = 60
    w = MAP_W - 2 * margin
    h = HEIGHT - y0 - margin

    # Border for the -180..180 / -90..90 extent.
    draw.rectangle([x0, y0, x0 + w, y0 + h], outline=BLACK, width=2)

    # Graticule every 30° of longitude / latitude.
    for lon in range(-150, 180, 30):
        x, _ = _lonlat_to_xy(lon, 0, x0, y0, w, h)
        draw.line([(x, y0), (x, y0 + h)], fill=LIGHT_GRAY, width=1)
    for lat in range(-60, 90, 30):
        _, y = _lonlat_to_xy(0, lat, x0, y0, w, h)
        draw.line([(x0, y), (x0 + w, y)], fill=LIGHT_GRAY, width=1)

    # Equator + prime meridian slightly darker for orientation.
    _, eq_y = _lonlat_to_xy(0, 0, x0, y0, w, h)
    pm_x, _ = _lonlat_to_xy(0, 0, x0, y0, w, h)
    draw.line([(x0, eq_y), (x0 + w, eq_y)], fill=GRAY, width=1)
    draw.line([(pm_x, y0), (pm_x, y0 + h)], fill=GRAY, width=1)

    ix, iy = _lonlat_to_xy(iss_lon, iss_lat, x0, y0, w, h)
    ox, oy = _lonlat_to_xy(obs_lon, obs_lat, x0, y0, w, h)

    # Connecting line between observer and ISS sub-point.
    draw.line([(ox, oy), (ix, iy)], fill=BLUE, width=1)

    # Observer marker: small triangle.
    t = 7
    draw.polygon(
        [(ox, oy - t), (ox - t, oy + t), (ox + t, oy + t)],
        fill=BLUE, outline=BLACK,
    )

    # ISS marker: bold red dot + label.
    r = 7
    draw.ellipse([ix - r, iy - r, ix + r, iy + r], fill=RED, outline=BLACK)
    label_font = get_font(16, bold=True, config=config)
    lw = _text_w(draw, "ISS", label_font)
    lx = ix + r + 3
    if lx + lw > x0 + w:            # keep label inside the map
        lx = ix - r - 3 - lw
    ly = iy - 8
    ly = max(y0 + 1, min(ly, y0 + h - 16))
    draw.text((lx, ly), "ISS", font=label_font, fill=RED)


def _draw_panel(draw: ImageDraw.ImageDraw, config: dict, data: dict,
                distance_km: float, bearing: float,
                stale: bool) -> None:
    # Panel background + divider.
    draw.rectangle([PANEL_X, 0, WIDTH, HEIGHT], fill=PANEL_BG)
    draw.line([(PANEL_X, 0), (PANEL_X, HEIGHT)], fill=BLACK, width=2)

    pad = 16
    left = PANEL_X + pad
    y = 18

    header_font = get_font(30, bold=True, config=config)
    draw.text((left, y), "ISS Tracker", font=header_font, fill=BLACK)
    y += 40
    draw.line([(left, y), (WIDTH - pad, y)], fill=GRAY, width=1)
    y += 14

    label_font = get_font(16, bold=True, config=config)
    value_font = get_font(22, bold=True, config=config)

    visibility = data.get("visibility", "").lower()
    vis_text = "Daylight" if visibility == "daylight" else (
        "Eclipsed" if visibility == "eclipsed" else visibility.title() or "—")

    bearing_text = f"{int(round(bearing))}° {_compass_point(bearing)}"

    rows = [
        ("Altitude", f"{data['altitude']:.0f} km"),
        ("Velocity", f"{data['velocity']:,.0f} km/h"),
        ("Visibility", vis_text),
        ("Distance", f"{distance_km:,.0f} km"),
        ("Bearing", bearing_text),
        ("Sub-point lat", f"{data['latitude']:.2f}°"),
        ("Sub-point lon", f"{data['longitude']:.2f}°"),
    ]

    for label, value in rows:
        draw.text((left, y), label.upper(), font=label_font, fill=GRAY)
        y += 20
        draw.text((left, y), value, font=value_font, fill=BLACK)
        y += 30

    # Update time (local) at the bottom.
    ts = data.get("timestamp", data.get("fetched_at", time.time()))
    local = datetime.fromtimestamp(ts).strftime("%b %d, %I:%M %p")
    small_font = get_font(14, bold=False, config=config)
    footer = ("Stale: " + local) if stale else ("Updated " + local)
    draw.text((left, HEIGHT - 26), footer, font=small_font, fill=GRAY)


# ── Entry point ──────────────────────────────────────────────────────────────

def generate(config: dict) -> str:
    """
    Generate the ISS tracker display image and return the output BMP path.

    Config section:
        iss_tracker:
          output_path: images/iss_display.bmp
          cache_dir: data/
    """
    cfg = config.get("iss_tracker", {})
    loc = config.get("forecast_location", {})

    output_path = cfg.get("output_path", "images/iss_display.bmp")
    cache_dir = cfg.get("cache_dir", "data/")

    obs_lat = float(loc.get("latitude", 0.0))
    obs_lon = float(loc.get("longitude", 0.0))

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    img = Image.new("RGB", (WIDTH, HEIGHT), WHITE)

    data = _get_data(cache_dir)

    if data is None:
        _draw_unavailable(img, config)
        img.save(output_path)
        logger.info("Saved ISS unavailable display to %s", output_path)
        return output_path

    draw = ImageDraw.Draw(img)

    iss_lat = data["latitude"]
    iss_lon = data["longitude"]
    distance_km = _haversine_km(obs_lat, obs_lon, iss_lat, iss_lon)
    bearing = _bearing_deg(obs_lat, obs_lon, iss_lat, iss_lon)
    stale = bool(data.get("stale", False))

    _draw_map(draw, config, iss_lon, iss_lat, obs_lon, obs_lat)
    _draw_panel(draw, config, data, distance_km, bearing, stale)

    img.save(output_path)
    logger.info(
        "Saved ISS tracker to %s (lat=%.2f lon=%.2f dist=%.0fkm)",
        output_path, iss_lat, iss_lon, distance_km,
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
