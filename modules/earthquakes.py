"""
Recent Earthquakes Module — E-ink display showing a world map of recent
earthquakes plus a ranked list of the strongest events, sourced from the
keyless USGS earthquake GeoJSON feeds.

800×480 pixels, output as BMP.

Config section (add to config.yml):

    earthquakes:
      output_path: images/earthquakes_display.bmp
      feed: "2.5_day"    # 2.5_day | 4.5_day | significant_week | all_day
      cache_dir: data/

Reads the shared forecast_location block for the observer latitude/longitude/name
(used to compute distance from each quake and to mark the map):

    forecast_location:
      latitude: 35.9251
      longitude: -86.8689
      name: "Franklin, TN"

Data source (no API key required):
    https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/{feed}.geojson

Each GeoJSON feature exposes:
    properties.mag           magnitude
    properties.place         human-readable location string
    properties.time          event time (ms since epoch)
    geometry.coordinates     [lon, lat, depth_km]

Magnitude color scale (e-ink colors):
    < 3    Green  (0,   255,   0)
    3 – 5  Yellow (255, 255,   0)
    5 – 6  Orange (255, 128,   0)
    6+     Red    (255,   0,   0)
"""

import json
import math
import os
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import requests
from PIL import Image, ImageDraw

# Allow running directly from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import get_font, get_logger

logger = get_logger("earthquakes")

# ── Constants ────────────────────────────────────────────────────────────────

WIDTH = 800
HEIGHT = 480
CACHE_TTL = 900          # 15 minutes in seconds

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
GRAY = (120, 120, 120)
LGRAY = (200, 200, 200)

GREEN = (0, 255, 0)
YELLOW = (255, 255, 0)
ORANGE = (255, 128, 0)
RED = (255, 0, 0)
BLUE = (0, 0, 255)

FEED_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/{feed}.geojson"

# Human-readable labels for the header, keyed by feed identifier.
FEED_LABELS = {
    "2.5_day": "M2.5+ · 24h",
    "4.5_day": "M4.5+ · 24h",
    "significant_week": "Significant · 7d",
    "all_day": "All · 24h",
}

# Layout geometry.
HEADER_H = 40
FOOTER_H = 26
MAP_X = 8
MAP_Y = HEADER_H + 8
MAP_W = 484
MAP_H = HEIGHT - HEADER_H - FOOTER_H - 16
LIST_X = MAP_X + MAP_W + 12          # ≈ 504
LIST_W = WIDTH - LIST_X - 8          # ≈ 288


# ── Magnitude helpers ────────────────────────────────────────────────────────

def _mag_color(mag: float) -> Tuple[int, int, int]:
    """Return the e-ink RGB color for the given magnitude."""
    if mag < 3:
        return GREEN
    if mag < 5:
        return YELLOW
    if mag < 6:
        return ORANGE
    return RED


def _mag_radius(mag: float) -> int:
    """Marker radius (px) scaling with magnitude."""
    return int(round(2 + max(mag, 0.0) * 1.5))


# ── Distance (haversine, stdlib math only) ───────────────────────────────────

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometers between two lat/lon points."""
    r = 6371.0088
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    )
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _ago(event_ms: float, now_s: float) -> str:
    """Return a compact 'time ago' string for an event time in ms epoch."""
    secs = now_s - (event_ms / 1000.0)
    if secs < 0:
        secs = 0
    mins = secs / 60.0
    if mins < 60:
        return f"{int(mins)}m ago"
    hours = mins / 60.0
    if hours < 24:
        return f"{int(hours)}h ago"
    days = hours / 24.0
    return f"{int(days)}d ago"


# ── Cache ────────────────────────────────────────────────────────────────────

def _cache_path(cache_dir: str) -> str:
    return os.path.join(cache_dir, "earthquakes_cache.json")


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
        logger.warning("Failed to write earthquakes cache: %s", exc)


# ── Fetch ────────────────────────────────────────────────────────────────────

def _fetch_quakes(feed: str) -> Optional[List[dict]]:
    """Fetch and normalize earthquake features from the USGS feed.

    Returns a list of dicts: {"mag", "place", "time", "lon", "lat", "depth"}.
    Returns None on any network/parse failure.
    """
    url = FEED_URL.format(feed=feed)
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Failed to fetch earthquakes feed '%s': %s", feed, exc)
        return None

    features = data.get("features")
    if not isinstance(features, list):
        logger.warning("Unexpected earthquakes GeoJSON structure")
        return None

    quakes: List[dict] = []
    for feat in features:
        try:
            props = feat.get("properties", {})
            coords = feat.get("geometry", {}).get("coordinates", [])
            mag = props.get("mag")
            if mag is None or len(coords) < 2:
                continue
            quakes.append({
                "mag": float(mag),
                "place": props.get("place") or "Unknown location",
                "time": float(props.get("time", 0)),
                "lon": float(coords[0]),
                "lat": float(coords[1]),
                "depth": float(coords[2]) if len(coords) > 2 and coords[2] is not None else 0.0,
            })
        except (TypeError, ValueError):
            continue

    return quakes


def _get_data(cache_dir: str, feed: str) -> Optional[dict]:
    """Return quake data, using a fresh fetch, then falling back to cache.

    Payload: {"feed": str, "quakes": [...], "fetched_at": ts}
    """
    cache = _load_cache(cache_dir)
    now = time.time()

    if (
        cache
        and cache.get("feed") == feed
        and (now - cache.get("fetched_at", 0)) < CACHE_TTL
    ):
        logger.info("Using cached earthquakes data")
        return cache

    quakes = _fetch_quakes(feed)
    if quakes is not None:
        payload = {"feed": feed, "quakes": quakes, "fetched_at": now}
        _save_cache(cache_dir, payload)
        return payload

    if cache:
        logger.warning("Fetch failed; falling back to stale earthquakes cache")
        return cache

    return None


# ── Drawing helpers ──────────────────────────────────────────────────────────

def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _truncate(draw, text, font, max_w) -> str:
    """Truncate text with an ellipsis so it fits within max_w pixels."""
    if _text_w(draw, text, font) <= max_w:
        return text
    ell = "…"
    while text and _text_w(draw, text + ell, font) > max_w:
        text = text[:-1]
    return (text + ell) if text else ell


def _draw_unavailable(img, config) -> None:
    draw = ImageDraw.Draw(img)
    font = get_font(40, bold=True, config=config)
    msg = "Earthquake data unavailable"
    w = _text_w(draw, msg, font)
    bbox = draw.textbbox((0, 0), msg, font=font)
    h = bbox[3] - bbox[1]
    draw.text(((WIDTH - w) / 2, (HEIGHT - h) / 2 - bbox[1]), msg, font=font, fill=GRAY)


def _lonlat_to_xy(lon: float, lat: float) -> Tuple[float, float]:
    """Equirectangular projection into the map rectangle."""
    x = MAP_X + (lon + 180) / 360.0 * MAP_W
    y = MAP_Y + (90 - lat) / 180.0 * MAP_H
    return x, y


def _draw_map(draw, quakes: List[dict], obs_lat, obs_lon, has_obs: bool) -> None:
    """Draw the graticule, border, quakes, and observer marker."""
    # Border.
    draw.rectangle([MAP_X, MAP_Y, MAP_X + MAP_W, MAP_Y + MAP_H], outline=BLACK, width=1)

    # Graticule every 30°.
    for lon in range(-150, 180, 30):
        x, _ = _lonlat_to_xy(lon, 0)
        draw.line([x, MAP_Y, x, MAP_Y + MAP_H], fill=LGRAY, width=1)
    for lat in range(-60, 90, 30):
        _, y = _lonlat_to_xy(0, lat)
        draw.line([MAP_X, y, MAP_X + MAP_W, y], fill=LGRAY, width=1)

    # Equator + prime meridian slightly stronger.
    _, eq_y = _lonlat_to_xy(0, 0)
    draw.line([MAP_X, eq_y, MAP_X + MAP_W, eq_y], fill=GRAY, width=1)
    pm_x, _ = _lonlat_to_xy(0, 0)
    draw.line([pm_x, MAP_Y, pm_x, MAP_Y + MAP_H], fill=GRAY, width=1)

    # Quakes: draw weakest first so strong (red) ones land on top.
    for q in sorted(quakes, key=lambda z: z["mag"]):
        x, y = _lonlat_to_xy(q["lon"], q["lat"])
        r = _mag_radius(q["mag"])
        draw.ellipse([x - r, y - r, x + r, y + r], fill=_mag_color(q["mag"]), outline=BLACK)

    # Observer marker: blue cross-hair diamond.
    if has_obs:
        ox, oy = _lonlat_to_xy(obs_lon, obs_lat)
        s = 6
        draw.line([ox - s, oy, ox + s, oy], fill=BLUE, width=2)
        draw.line([ox, oy - s, ox, oy + s], fill=BLUE, width=2)
        draw.ellipse([ox - 3, oy - 3, ox + 3, oy + 3], outline=BLUE, width=2)


def _draw_list(draw, quakes, obs_lat, obs_lon, has_obs, now_s, config) -> None:
    """Draw the ranked list of the strongest quakes on the right column."""
    strongest = sorted(quakes, key=lambda z: z["mag"], reverse=True)[:7]

    mag_font = get_font(22, bold=True, config=config)
    place_font = get_font(17, bold=False, config=config)
    meta_font = get_font(14, bold=False, config=config)

    y = MAP_Y
    row_h = (MAP_H) / 7.0
    badge_w = 52
    for i, q in enumerate(strongest):
        ry = y + i * row_h
        mag = q["mag"]
        color = _mag_color(mag)

        # Magnitude badge.
        badge_h = row_h - 8
        draw.rectangle(
            [LIST_X, ry + 2, LIST_X + badge_w, ry + 2 + badge_h],
            fill=color,
            outline=BLACK,
            width=1,
        )
        mag_str = f"{mag:.1f}"
        mw = _text_w(draw, mag_str, mag_font)
        mbbox = draw.textbbox((0, 0), mag_str, font=mag_font)
        mh = mbbox[3] - mbbox[1]
        draw.text(
            (LIST_X + (badge_w - mw) / 2, ry + 2 + (badge_h - mh) / 2 - mbbox[1]),
            mag_str,
            font=mag_font,
            fill=BLACK,
        )

        # Place + meta text.
        tx = LIST_X + badge_w + 8
        avail_w = LIST_W - badge_w - 10
        place = _truncate(draw, q["place"], place_font, avail_w)
        draw.text((tx, ry + 3), place, font=place_font, fill=BLACK)

        ago = _ago(q["time"], now_s)
        meta = ago
        if has_obs:
            d_km = _haversine_km(obs_lat, obs_lon, q["lat"], q["lon"])
            d_mi = d_km * 0.621371
            meta = f"{ago}  ·  {int(round(d_km))} km / {int(round(d_mi))} mi"
        meta = _truncate(draw, meta, meta_font, avail_w)
        draw.text((tx, ry + 3 + 20), meta, font=meta_font, fill=GRAY)


# ── Main entry point ─────────────────────────────────────────────────────────

def generate(config: dict) -> str:
    """
    Generate the recent-earthquakes display image and return the output BMP path.

    Config section:
        earthquakes:
          output_path: images/earthquakes_display.bmp
          feed: "2.5_day"    # 2.5_day | 4.5_day | significant_week | all_day
          cache_dir: data/
    """
    cfg = config.get("earthquakes", {})
    loc = config.get("forecast_location", {})

    output_path = cfg.get("output_path", "images/earthquakes_display.bmp")
    cache_dir = cfg.get("cache_dir", "data/")
    feed = cfg.get("feed", "2.5_day")

    has_obs = "latitude" in loc and "longitude" in loc
    obs_lat = float(loc.get("latitude", 0.0))
    obs_lon = float(loc.get("longitude", 0.0))

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    img = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(img)

    data = _get_data(cache_dir, feed)

    if data is None or not data.get("quakes"):
        # No cache and fetch failed → unavailable notice. An empty feed (a
        # successful fetch that simply returned zero quakes) still renders the
        # frame with a "0" count rather than an error.
        if data is None:
            _draw_unavailable(img, config)
            img.save(output_path)
            logger.info("Saved earthquakes unavailable display to %s", output_path)
            return output_path

    quakes = data.get("quakes", [])
    fetched_at = data.get("fetched_at", 0)
    now_s = time.time()

    # ── Header bar ───────────────────────────────────────────────────────────
    draw.rectangle([0, 0, WIDTH, HEADER_H], fill=BLACK)
    title_font = get_font(26, bold=True, config=config)
    draw.text((10, (HEADER_H - 26) / 2 - 2), "Recent Earthquakes", font=title_font, fill=WHITE)
    label = FEED_LABELS.get(feed, feed)
    label_font = get_font(20, bold=False, config=config)
    lw = _text_w(draw, label, label_font)
    draw.text((WIDTH - lw - 10, (HEADER_H - 20) / 2 - 1), label, font=label_font, fill=WHITE)

    # ── Map ──────────────────────────────────────────────────────────────────
    _draw_map(draw, quakes, obs_lat, obs_lon, has_obs)

    # ── List ─────────────────────────────────────────────────────────────────
    if quakes:
        _draw_list(draw, quakes, obs_lat, obs_lon, has_obs, now_s, config)
    else:
        empty_font = get_font(20, bold=False, config=config)
        draw.text((LIST_X, MAP_Y + 10), "No recent quakes", font=empty_font, fill=GRAY)

    # ── Footer ───────────────────────────────────────────────────────────────
    footer_font = get_font(16, bold=False, config=config)
    if fetched_at:
        upd = datetime.fromtimestamp(fetched_at).strftime("%b %d %I:%M %p")
    else:
        upd = "unknown"
    footer = f"{len(quakes)} quakes  •  USGS  •  Updated {upd}"
    fw = _text_w(draw, footer, footer_font)
    draw.text(((WIDTH - fw) / 2, HEIGHT - FOOTER_H + 4), footer, font=footer_font, fill=GRAY)

    img.save(output_path)
    logger.info(
        "Saved earthquakes display (%d quakes, feed=%s) to %s",
        len(quakes), feed, output_path,
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
