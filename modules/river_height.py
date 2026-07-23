"""
River Height Display Module — E-ink display showing current river gauge height
from the USGS Water Services API (free, no key required).

Shows current stage, flood category, and a 48-hour history chart.

800×480 pixels, output as BMP.

Config section (add to config.yml):

    river_height:
      output_path: images/river_display.bmp
      cache_dir: data/
      site_number: "03430500"      # USGS gauge site number
      gauge_name: "Harpeth River at Franklin"
      # Flood stage thresholds (feet). Leave 0 to fetch from NWS automatically.
      action_stage: 0.0
      flood_stage: 0.0
      moderate_flood_stage: 0.0
      major_flood_stage: 0.0
      nws_lid: ""                  # NWS location ID (e.g. FKLT1) for auto thresholds
      history_hours: 48            # hours of history to show in chart

Stage color bands:
    Below action   → Blue   (0, 100, 220)
    Action stage   → Yellow (255, 220, 0)
    Flood stage    → Orange (255, 128, 0)
    Moderate flood → Red    (220, 0,  0)
    Major flood    → Purple (150, 0, 150)
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import requests
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import get_font, get_logger

logger = get_logger("river_height")

WIDTH = 800
HEIGHT = 480
GRAY = (120, 120, 120)
CACHE_TTL = 900  # 15 minutes

USGS_IV_URL = (
    "https://waterservices.usgs.gov/nwis/iv/"
    "?format=json&sites={site}&parameterCd=00065&period=P{days}D"
)
NWS_GAUGE_URL = "https://api.water.noaa.gov/nwps/v1/gauges/{lid}"

# Stage category definitions: (threshold_key, label, color)
# threshold is the minimum stage value to match this category.
# Evaluated from highest to lowest; "Normal" is the fallback.
STAGE_CATS = [
    ("major_flood_stage",    "Major Flood",    (150,  0, 150)),
    ("moderate_flood_stage", "Moderate Flood", (220,  0,   0)),
    ("flood_stage",          "Flood Stage",    (255, 128,   0)),
    ("action_stage",         "Action Stage",   (255, 220,   0)),
]
NORMAL_COLOR = (0, 100, 220)


# ── Stage helpers ─────────────────────────────────────────────────────────────

def _stage_color_and_label(
    stage_ft: float, thresholds: dict
) -> Tuple[Tuple[int, int, int], str]:
    for key, label, color in STAGE_CATS:
        thresh = thresholds.get(key, 0.0) or 0.0
        if thresh > 0 and stage_ft >= thresh:
            return color, label
    return NORMAL_COLOR, "Normal"


# ── Cache ─────────────────────────────────────────────────────────────────────

def _cache_path(cache_dir: str) -> str:
    return os.path.join(cache_dir, "river_cache.json")


def _load_cache(cache_dir: str) -> Optional[dict]:
    try:
        with open(_cache_path(cache_dir)) as f:
            return json.load(f)
    except Exception:
        return None


def _save_cache(cache_dir: str, payload: dict) -> None:
    os.makedirs(cache_dir, exist_ok=True)
    try:
        with open(_cache_path(cache_dir), "w") as f:
            json.dump(payload, f)
    except Exception as e:
        logger.warning("Could not write river cache: %s", e)


# ── NWS flood stage fetch ─────────────────────────────────────────────────────

def _fetch_nws_stages(nws_lid: str) -> dict:
    """
    Fetch flood stage thresholds from the NWS Water Prediction Service.
    Returns a dict with keys: action_stage, flood_stage, moderate_flood_stage,
    major_flood_stage (float feet). Missing values are 0.0.
    """
    try:
        resp = requests.get(NWS_GAUGE_URL.format(lid=nws_lid), timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("NWS stages fetch failed for %s: %s", nws_lid, e)
        return {}

    # NWS API nests stages under data.flood.categories
    try:
        cats = data.get("flood", {}).get("categories", [])
        stages = {}
        key_map = {
            "action":   "action_stage",
            "minor":    "flood_stage",
            "moderate": "moderate_flood_stage",
            "major":    "major_flood_stage",
        }
        for cat in cats:
            name = cat.get("name", "").lower()
            stage = cat.get("stage")
            if name in key_map and stage is not None:
                stages[key_map[name]] = float(stage)
        logger.info("NWS stages for %s: %s", nws_lid, stages)
        return stages
    except Exception as e:
        logger.warning("Could not parse NWS stages: %s", e)
        return {}


# ── USGS data fetch ───────────────────────────────────────────────────────────

def _fetch_usgs(site: str, history_days: int) -> Optional[dict]:
    """
    Fetch gauge height readings from USGS for the past `history_hours`.
    Returns dict with keys: current_ft, history (list of {ts, ft}), fetched_at.
    Returns None on failure.
    """
    url = USGS_IV_URL.format(site=site, days=history_days)
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        logger.error("USGS fetch failed for site %s: %s", site, e)
        return None

    try:
        ts_series = raw["value"]["timeSeries"]
        if not ts_series:
            logger.warning("USGS returned no time series for site %s", site)
            return None

        values = ts_series[0]["values"][0]["value"]
        if not values:
            logger.warning("USGS time series is empty for site %s", site)
            return None

        history = []
        for v in values:
            try:
                ft = float(v["value"])
                # USGS timestamps: "2024-01-15T12:30:00.000-06:00"
                ts_str = v["dateTime"]
                history.append({"ts": ts_str, "ft": ft})
            except (ValueError, KeyError):
                continue

        if not history:
            logger.warning("No valid readings for site %s", site)
            return None

        current_ft = history[-1]["ft"]
        return {
            "current_ft": current_ft,
            "history": history,
            "fetched_at": time.time(),
        }
    except (KeyError, IndexError, TypeError) as e:
        logger.error("Could not parse USGS response: %s", e)
        return None


def _get_river_data(cfg: dict, cache_dir: str) -> Optional[dict]:
    """Return river data using a 15-minute cache. Falls back to stale cache."""
    os.makedirs(cache_dir, exist_ok=True)
    cache = _load_cache(cache_dir)

    if cache and (time.time() - cache.get("fetched_at", 0)) < CACHE_TTL:
        logger.info("Using cached river data (age: %.0fs)",
                    time.time() - cache["fetched_at"])
        return cache

    site = str(cfg.get("site_number", "")).strip()
    if not site:
        logger.warning("river_height: site_number not configured")
        return cache  # stale or None

    history_hours = int(cfg.get("history_hours", 48))
    history_days = max(1, (history_hours + 23) // 24)
    data = _fetch_usgs(site, history_days)
    if data:
        _save_cache(cache_dir, data)
        logger.info("Fetched USGS gauge %.2f ft (site %s)", data["current_ft"], site)
        return data

    logger.warning("USGS fetch failed — trying stale cache")
    if cache:
        age_min = (time.time() - cache.get("fetched_at", 0)) / 60
        logger.warning("Using stale river cache (age: %.0f min)", age_min)
        return cache

    return None


def _resolve_thresholds(cfg: dict) -> dict:
    """
    Return flood stage thresholds. Prefers values from config;
    fills in missing ones from NWS if a nws_lid is configured.
    """
    keys = ["action_stage", "flood_stage", "moderate_flood_stage", "major_flood_stage"]
    thresholds = {k: float(cfg.get(k) or 0.0) for k in keys}

    nws_lid = str(cfg.get("nws_lid", "")).strip()
    missing = [k for k in keys if thresholds[k] <= 0.0]

    if missing and nws_lid:
        nws = _fetch_nws_stages(nws_lid)
        for k in missing:
            if k in nws:
                thresholds[k] = nws[k]

    return thresholds


# ── Drawing helpers ───────────────────────────────────────────────────────────

def _draw_centered(draw: ImageDraw.ImageDraw, text: str, font,
                   y: int, color, x0: int = 0, w: int = WIDTH) -> int:
    """Draw text horizontally centered in [x0, x0+w). Returns text height."""
    bb = draw.textbbox((0, 0), text, font=font)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    draw.text((x0 + (w - tw) // 2 - bb[0], y), text, fill=color, font=font)
    return th


def _fit_number(draw: ImageDraw.ImageDraw, text: str, config: dict,
                max_w: int, max_h: int):
    """Return the largest bold font that fits text within max_w × max_h."""
    best = get_font(10, bold=True, config=config)
    for size in range(max_h, 20, -4):
        font = get_font(size, bold=True, config=config)
        bb = draw.textbbox((0, 0), text, font=font)
        if (bb[2] - bb[0]) <= max_w and (bb[3] - bb[1]) <= max_h:
            return font
    return best


def _draw_history_chart(
    draw: ImageDraw.ImageDraw,
    history: List[dict],
    thresholds: dict,
    config: dict,
    chart_top: int,
    chart_bottom: int,
) -> None:
    """Draw a colored bar chart of gauge height history."""
    if len(history) < 2:
        return

    margin_x = 40
    chart_w = WIDTH - 2 * margin_x
    chart_h = chart_bottom - chart_top

    values = [h["ft"] for h in history]
    vmin = min(values)
    vmax = max(values) or 1.0
    span = vmax - vmin or 1.0
    n = len(values)
    bar_w = chart_w / n

    # Baseline
    draw.line(
        [(margin_x, chart_bottom), (margin_x + chart_w, chart_bottom)],
        fill=(180, 180, 180), width=1,
    )

    for i, v in enumerate(values):
        h_px = max(1, int(((v - vmin) / span) * (chart_h - 4)) + 4)
        x0 = int(margin_x + i * bar_w)
        x1 = max(x0 + 1, int(margin_x + (i + 1) * bar_w) - 1)
        y0 = chart_bottom - h_px
        color, _ = _stage_color_and_label(v, thresholds)
        draw.rectangle([(x0, y0), (x1, chart_bottom)], fill=color)

    # Draw threshold lines if within the plotted range
    label_font = get_font(11, config=config)
    for key, label, color in STAGE_CATS:
        thresh = thresholds.get(key, 0.0) or 0.0
        if thresh <= 0 or thresh < vmin or thresh > vmax * 1.05:
            continue
        frac = (thresh - vmin) / span
        y = int(chart_bottom - frac * (chart_h - 4) - 4)
        draw.line([(margin_x, y), (margin_x + chart_w, y)], fill=color, width=1)
        short_label = label.replace(" Flood", "").replace("Action", "Act.")
        draw.text((margin_x + 2, y - 12), short_label, fill=color, font=label_font)

    # X-axis time labels: oldest and newest
    ts_font = get_font(11, config=config)
    try:
        oldest_ts = history[0]["ts"][:16].replace("T", " ")
        newest_ts = history[-1]["ts"][:16].replace("T", " ")
        draw.text((margin_x, chart_bottom + 2), oldest_ts, fill=GRAY, font=ts_font)
        newest_bb = draw.textbbox((0, 0), newest_ts, font=ts_font)
        newest_w = newest_bb[2] - newest_bb[0]
        draw.text(
            (margin_x + chart_w - newest_w, chart_bottom + 2),
            newest_ts, fill=GRAY, font=ts_font,
        )
    except Exception:
        pass


def _draw_unavailable(img: Image.Image, config: dict) -> None:
    draw = ImageDraw.Draw(img)
    font = get_font(40, bold=True, config=config)
    msg = "River Data Unavailable"
    bb = draw.textbbox((0, 0), msg, font=font)
    x = (WIDTH - (bb[2] - bb[0])) // 2
    y = (HEIGHT - (bb[3] - bb[1])) // 2
    draw.text((x, y), msg, fill=(120, 120, 120), font=font)
    sub_font = get_font(16, config=config)
    hint = "Check site_number in config"
    hbb = draw.textbbox((0, 0), hint, font=sub_font)
    hx = (WIDTH - (hbb[2] - hbb[0])) // 2
    hy = y + (bb[3] - bb[1]) + 12
    draw.text((hx, hy), hint, fill=(160, 160, 160), font=sub_font)


# ── Main generate() ───────────────────────────────────────────────────────────

def generate(config: dict) -> str:
    """
    Generate the river height display image and return the output BMP path.

    Config section (river_height:):
      output_path, cache_dir, site_number, gauge_name, nws_lid,
      action_stage, flood_stage, moderate_flood_stage, major_flood_stage,
      history_hours
    """
    cfg = config.get("river_height", {})
    output_path = cfg.get("output_path", "images/river_display.bmp")
    cache_dir   = cfg.get("cache_dir", "data/")
    gauge_name  = cfg.get("gauge_name", "River Gauge")
    site_number = str(cfg.get("site_number", "")).strip()

    out_dir = os.path.dirname(output_path)
    os.makedirs(out_dir if out_dir else ".", exist_ok=True)
    os.makedirs(cache_dir, exist_ok=True)

    data = _get_river_data(cfg, cache_dir)
    thresholds = _resolve_thresholds(cfg)

    img = Image.new("RGB", (WIDTH, HEIGHT), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    if data is None:
        _draw_unavailable(img, config)
        img.save(output_path)
        logger.info("Saved river-unavailable display to %s", output_path)
        return output_path

    current_ft = data["current_ft"]
    history    = data.get("history", [])
    fetched_at = data.get("fetched_at", 0)

    color, status = _stage_color_and_label(current_ft, thresholds)
    value_str = f"{current_ft:.2f}"

    has_chart = len(history) >= 2
    chart_top    = HEIGHT - 155 if has_chart else HEIGHT - 40
    chart_bottom = HEIGHT - 28  if has_chart else HEIGHT - 28

    # ── Title ──────────────────────────────────────────────────────────────
    title_font = get_font(28, bold=True, config=config)
    _draw_centered(draw, "River Stage", title_font, 10, (0, 0, 0))

    name_font = get_font(19, config=config)
    _draw_centered(draw, gauge_name, name_font, 44, GRAY)

    # ── Giant stage number ─────────────────────────────────────────────────
    num_top  = 76
    num_area_h = chart_top - num_top - 70  # leave room for unit + status
    num_font = _fit_number(draw, value_str, config, WIDTH - 80, num_area_h)
    nb = draw.textbbox((0, 0), value_str, font=num_font)
    n_h = nb[3] - nb[1]
    n_y = num_top + (num_area_h - n_h) // 2
    _draw_centered(draw, value_str, num_font, n_y, color)

    # ── Unit label ─────────────────────────────────────────────────────────
    unit_font = get_font(22, config=config)
    unit_y = n_y + n_h + 6
    u_h = _draw_centered(draw, "feet", unit_font, unit_y, GRAY)

    # ── Status label ───────────────────────────────────────────────────────
    status_font = get_font(36, bold=True, config=config)
    status_y = unit_y + u_h + 10
    _draw_centered(draw, status, status_font, status_y, color)

    # ── History chart ──────────────────────────────────────────────────────
    if has_chart:
        _draw_history_chart(draw, history, thresholds, config, chart_top, chart_bottom)

    # ── Footer ─────────────────────────────────────────────────────────────
    ts = datetime.fromtimestamp(fetched_at or time.time()).strftime("%-I:%M %p")
    site_label = f"USGS {site_number}" if site_number else "USGS"
    footer = f"{site_label}  ·  Updated {ts}"
    footer_font = get_font(13, config=config)
    fb = draw.textbbox((0, 0), footer, font=footer_font)
    draw.text(
        (WIDTH - (fb[2] - fb[0]) - 8, HEIGHT - (fb[3] - fb[1]) - 6),
        footer, fill=GRAY, font=footer_font,
    )

    img.save(output_path)
    logger.info("Saved river display (%.2f ft, %s) to %s", current_ft, status, output_path)
    return output_path


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import yaml

    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yml"
    )
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    result = generate(cfg)
    print(f"Generated: {result}")
