"""
Grid Carbon Intensity Display Module — E-ink display showing the current
electricity-grid carbon intensity (gCO2/kWh) for a configured region.

800×480 pixels, output as BMP.

Two data providers are supported via the ``provider`` config key:

    1. uk  (KEYLESS, default) — UK Carbon Intensity API
         https://api.carbonintensity.org.uk/intensity        (national current)
         https://api.carbonintensity.org.uk/intensity/date    (24h half-hourly forecast)
    2. electricitymaps (needs a free token) — ElectricityMaps latest endpoint
         https://api.electricitymap.org/v3/carbon-intensity/latest?zone={zone}
         with header  auth-token: {token}
       If the token is empty, this provider falls back to the keyless ``uk`` provider.

Config section (add to config.yml):

    carbon_intensity:
      output_path: images/carbon_display.bmp
      provider: uk               # uk (keyless) | electricitymaps
      em_zone: "US-TEN-TVA"      # ElectricityMaps zone (only for electricitymaps)
      em_token: ""               # ElectricityMaps free token
      cache_dir: data/

Intensity tiers (gCO2/kWh) and display colors (7-color e-ink palette):
      <100   Very Clean → Green  (0,   255, 0)
    100–200  Low        → Green  (0,   255, 0)
    200–300  Moderate   → Yellow (255, 255, 0)
    300–400  High       → Orange (255, 128, 0)
     >400    Very High  → Red    (255, 0,   0)
"""

import json
import os
import sys
import time
from datetime import datetime
from typing import List, Optional, Tuple

import requests
from PIL import Image, ImageDraw

# Allow running directly from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import get_font, get_logger

logger = get_logger("carbon_intensity")

# ── Constants ────────────────────────────────────────────────────────────────

WIDTH = 800
HEIGHT = 480
CACHE_TTL = 1800        # 30 minutes in seconds
GRAY = (120, 120, 120)

UK_CURRENT_URL = "https://api.carbonintensity.org.uk/intensity"
UK_FORECAST_URL = "https://api.carbonintensity.org.uk/intensity/date"
EM_URL = "https://api.electricitymap.org/v3/carbon-intensity/latest?zone={zone}"

# Intensity tier definitions: (max_exclusive, label, rgb_color)
# The final tier (Very High) has no upper bound.
CARBON_TIERS = [
    (100, "VERY CLEAN", (0,   255, 0)),
    (200, "LOW",        (0,   255, 0)),
    (300, "MODERATE",   (255, 255, 0)),
    (400, "HIGH",       (255, 128, 0)),
    (10**9, "VERY HIGH", (255, 0,  0)),
]


# ── Intensity helpers ─────────────────────────────────────────────────────────

def _carbon_color(value: float) -> Tuple[int, int, int]:
    """Return the e-ink RGB color for the given intensity in gCO2/kWh."""
    for hi, _label, color in CARBON_TIERS:
        if value < hi:
            return color
    return CARBON_TIERS[-1][2]


def _carbon_verdict(value: float) -> str:
    """Return the verdict word ("LOW"/"HIGH"/...) for the given intensity."""
    for hi, label, _color in CARBON_TIERS:
        if value < hi:
            return label
    return CARBON_TIERS[-1][1]


# ── Cache ─────────────────────────────────────────────────────────────────────

def _cache_path(cache_dir: str) -> str:
    return os.path.join(cache_dir, "carbon_cache.json")


def _load_cache(cache_dir: str) -> Optional[dict]:
    """Load cached carbon data if it exists. Returns dict or None."""
    try:
        with open(_cache_path(cache_dir), "r") as f:
            return json.load(f)
    except Exception:
        return None


def _save_cache(cache_dir: str, payload: dict) -> None:
    """Write carbon payload to cache file."""
    os.makedirs(cache_dir, exist_ok=True)
    try:
        with open(_cache_path(cache_dir), "w") as f:
            json.dump(payload, f)
    except Exception as e:
        logger.warning("Could not write carbon cache: %s", e)


# ── Data fetch ───────────────────────────────────────────────────────────────

def _fetch_uk() -> Optional[dict]:
    """
    Fetch current national carbon intensity + today's half-hourly forecast
    from the keyless UK Carbon Intensity API.

    Returns a dict with keys: intensity, index, region, provider, forecast,
    fetched_at. ``forecast`` is a list of {value, from} half-hourly entries.
    Returns None on failure of the (required) current-intensity call.
    """
    # ── Current national intensity ──────────────────────────────────────
    try:
        resp = requests.get(UK_CURRENT_URL, timeout=10,
                            headers={"Accept": "application/json"})
        resp.raise_for_status()
        entry = resp.json()["data"][0]["intensity"]
    except Exception as e:
        logger.error("UK current intensity fetch failed: %s", e)
        return None

    value = entry.get("actual")
    if value is None:
        value = entry.get("forecast")
    if value is None:
        logger.warning("UK API returned no actual/forecast intensity")
        return None

    index = entry.get("index", "")

    # ── 24h half-hourly forecast (best-effort; sparkline is optional) ───
    forecast: List[dict] = []
    try:
        f_resp = requests.get(UK_FORECAST_URL, timeout=10,
                            headers={"Accept": "application/json"})
        f_resp.raise_for_status()
        for slot in f_resp.json().get("data", []):
            fc = slot.get("intensity", {}).get("forecast")
            if fc is not None:
                forecast.append({"value": fc, "from": slot.get("from", "")})
    except Exception as e:
        logger.warning("UK forecast fetch failed (sparkline skipped): %s", e)

    return {
        "intensity":  float(value),
        "index":      index,
        "region":     "UK National",
        "provider":   "UK Carbon Intensity API",
        "forecast":   forecast,
        "fetched_at": time.time(),
    }


def _fetch_electricitymaps(zone: str, token: str) -> Optional[dict]:
    """
    Fetch latest carbon intensity for a zone from ElectricityMaps.
    Returns a dict (same shape as _fetch_uk, but with empty forecast).
    Returns None on failure.
    """
    try:
        resp = requests.get(
            EM_URL.format(zone=zone),
            headers={"auth-token": token},
            timeout=10,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        logger.error("ElectricityMaps fetch failed: %s", e)
        return None

    value = payload.get("carbonIntensity")
    if value is None:
        logger.warning("ElectricityMaps returned no carbonIntensity field")
        return None

    return {
        "intensity":  float(value),
        "index":      "",
        "region":     payload.get("zone", zone),
        "provider":   "ElectricityMaps",
        "forecast":   [],
        "fetched_at": time.time(),
    }


def _get_carbon_data(cfg: dict, cache_dir: str) -> Optional[dict]:
    """
    Return carbon data, using a 30-minute cache. On API failure, falls back
    to stale cache. Returns None only if no data is available at all.
    """
    os.makedirs(cache_dir, exist_ok=True)
    cache = _load_cache(cache_dir)

    # Return fresh cache if still valid
    if cache and (time.time() - cache.get("fetched_at", 0)) < CACHE_TTL:
        logger.info("Using cached carbon data (age: %.0fs)",
                    time.time() - cache["fetched_at"])
        return cache

    # Resolve provider (electricitymaps falls back to uk without a token)
    provider = str(cfg.get("provider", "uk")).strip().lower()
    token = str(cfg.get("em_token", "")).strip()
    zone = str(cfg.get("em_zone", "")).strip()

    data = None
    if provider == "electricitymaps":
        if token and zone:
            data = _fetch_electricitymaps(zone, token)
        else:
            logger.info("electricitymaps provider missing token/zone — "
                        "falling back to keyless UK provider")
            data = _fetch_uk()
    else:
        data = _fetch_uk()

    if data:
        _save_cache(cache_dir, data)
        logger.info("Fetched carbon intensity %.0f gCO2/kWh (%s)",
                    data["intensity"], data["region"])
        return data

    logger.warning("Live carbon fetch failed — trying stale cache")

    # Fall back to stale cache
    if cache:
        age_min = (time.time() - cache.get("fetched_at", 0)) / 60
        logger.warning("Using stale carbon cache (age: %.0f min)", age_min)
        return cache

    return None


# ── Cleanest-window analysis ──────────────────────────────────────────────────

def _cleanest_window(forecast: List[dict], span: int = 4) -> Optional[str]:
    """
    Find the contiguous ``span``-slot (2-hour) stretch with the lowest average
    forecast intensity and return a human label like "cleanest window: 2–4 AM".
    Returns None if the forecast is too short.
    """
    if len(forecast) < span:
        return None

    best_avg = None
    best_i = 0
    for i in range(len(forecast) - span + 1):
        window = forecast[i:i + span]
        avg = sum(w["value"] for w in window) / span
        if best_avg is None or avg < best_avg:
            best_avg = avg
            best_i = i

    def _fmt(iso: str) -> str:
        try:
            dt = datetime.strptime(iso, "%Y-%m-%dT%H:%MZ")
            return dt.strftime("%-I %p").strip()
        except Exception:
            return ""

    start = _fmt(forecast[best_i].get("from", ""))
    end = _fmt(forecast[min(best_i + span, len(forecast) - 1)].get("from", ""))
    if not start or not end:
        return None
    return f"cleanest window: {start}–{end}"


# ── Drawing helpers ───────────────────────────────────────────────────────────

def _fit_number(draw: ImageDraw.ImageDraw, text: str, config: dict,
                max_w: int, max_h: int) -> object:
    """Return the largest bold font that fits ``text`` within max_w × max_h."""
    best = get_font(10, bold=True, config=config)
    for size in range(max_h, 30, -4):
        font = get_font(size, bold=True, config=config)
        bb = draw.textbbox((0, 0), text, font=font)
        if (bb[2] - bb[0]) <= max_w and (bb[3] - bb[1]) <= max_h:
            return font
    return best


def _draw_centered(draw: ImageDraw.ImageDraw, text: str, font, y: int,
                found_color, x0: int = 0, w: int = WIDTH) -> int:
    """Draw ``text`` horizontally centered in [x0, x0+w). Returns text height."""
    bb = draw.textbbox((0, 0), text, font=font)
    tw = bb[2] - bb[0]
    th = bb[3] - bb[1]
    draw.text((x0 + (w - tw) // 2 - bb[0], y), text, fill=found_color, font=font)
    return th


def _draw_sparkline(draw: ImageDraw.ImageDraw, forecast: List[dict],
                    config: dict, top: int) -> None:
    """
    Draw a per-bar-colored bar chart of the half-hourly forecast across the
    bottom of the display, plus a "cleanest window" callout.
    """
    if not forecast:
        return

    margin_x = 40
    chart_w = WIDTH - 2 * margin_x
    chart_bottom = HEIGHT - 34          # leave room for footer
    chart_h = chart_bottom - top

    values = [f["value"] for f in forecast]
    vmax = max(values) or 1
    n = len(values)
    bar_w = chart_w / n

    # Baseline
    draw.line([(margin_x, chart_bottom), (margin_x + chart_w, chart_bottom)],
            fill=(200, 200, 200), width=1)

    for i, v in enumerate(values):
        h = int((v / vmax) * chart_h)
        x0 = int(margin_x + i * bar_w)
        x1 = int(margin_x + (i + 1) * bar_w) - 1
        if x1 <= x0:
            x1 = x0 + 1
        y0 = chart_bottom - h
        draw.rectangle([(x0, y0), (x1, chart_bottom)], fill=_carbon_color(v))

    # Cleanest-window callout above the chart
    callout = _cleanest_window(forecast)
    if callout:
        cf = get_font(18, bold=True, config=config)
        _draw_centered(draw, callout, cf, top - 26, (60, 120, 60))


def _draw_message(img: Image.Image, config: dict, msg: str) -> None:
    """Draw a centered fallback message (e.g. 'Carbon data unavailable')."""
    draw = ImageDraw.Draw(img)
    font = get_font(40, bold=True, config=config)
    bb = draw.textbbox((0, 0), msg, font=font)
    x = (WIDTH - (bb[2] - bb[0])) // 2
    y = (HEIGHT - (bb[3] - bb[1])) // 2
    draw.text((x, y), msg, fill=(120, 120, 120), font=font)


# ── Main generate() ───────────────────────────────────────────────────────────

def generate(config: dict) -> str:
    """
    Generate the carbon intensity display image and return the output BMP path.

    Reads config section:
        carbon_intensity:
          output_path: images/carbon_display.bmp
          provider: uk               # uk (keyless) | electricitymaps
          em_zone: "US-TEN-TVA"
          em_token: ""
          cache_dir: data/
    """
    cfg = config.get("carbon_intensity", {})
    output_path = cfg.get("output_path", "images/carbon_display.bmp")
    cache_dir = cfg.get("cache_dir", "data/")

    out_dir = os.path.dirname(output_path)
    os.makedirs(out_dir if out_dir else ".", exist_ok=True)
    os.makedirs(cache_dir, exist_ok=True)

    data = _get_carbon_data(cfg, cache_dir)

    img = Image.new("RGB", (WIDTH, HEIGHT), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    if data is None:
        _draw_message(img, config, "Carbon data unavailable")
        img.save(output_path)
        logger.info("Saved carbon-unavailable display to %s", output_path)
        return output_path

    value = data["intensity"]
    region = data.get("region", "")
    forecast = data.get("forecast", [])
    provider = data.get("provider", "")
    fetched_at = data.get("fetched_at", 0)

    color = _carbon_color(value)
    verdict = _carbon_verdict(value)
    value_str = str(int(round(value)))

    has_chart = bool(forecast)
    # Reserve bottom space for the chart when a forecast is present.
    chart_top = HEIGHT - 150 if has_chart else HEIGHT - 40

    # ── Title + region (top-center, bold) ──────────────────────────────────
    title_font = get_font(30, bold=True, config=config)
    _draw_centered(draw, "Grid Carbon Intensity", title_font, 18, (0, 0, 0))
    region_font = get_font(20, config=config)
    _draw_centered(draw, region, region_font, 54, GRAY)

    # ── Giant current intensity number ─────────────────────────────────────
    num_top = 86
    num_area_h = chart_top - num_top - 96   # leave room for unit + verdict
    num_font = _fit_number(draw, value_str, config, WIDTH - 80, num_area_h)
    nb = draw.textbbox((0, 0), value_str, font=num_font)
    n_h = nb[3] - nb[1]
    n_y = num_top + (num_area_h - n_h) // 2
    _draw_centered(draw, value_str, num_font, n_y, color)

    # ── Unit label below the number ────────────────────────────────────────
    unit_font = get_font(24, config=config)
    unit_y = n_y + n_h + 8
    u_h = _draw_centered(draw, "gCO₂/kWh", unit_font, unit_y, GRAY)

    # ── Verdict word ───────────────────────────────────────────────────────
    verdict_font = get_font(38, bold=True, config=config)
    verdict_y = unit_y + u_h + 14
    _draw_centered(draw, verdict, verdict_font, verdict_y, color)

    # ── Forecast sparkline (uk provider) ───────────────────────────────────
    if has_chart:
        _draw_sparkline(draw, forecast, config, chart_top)

    # ── Footer: provider + update time (gray, bottom-right) ────────────────
    ts = datetime.fromtimestamp(fetched_at or time.time()).strftime("%-I:%M %p")
    footer_text = f"{provider} · Updated {ts}"
    footer_font = get_font(13, config=config)
    fb = draw.textbbox((0, 0), footer_text, font=footer_font)
    draw.text((WIDTH - (fb[2] - fb[0]) - 8, HEIGHT - (fb[3] - fb[1]) - 6),
            footer_text, fill=GRAY, font=footer_font)

    img.save(output_path)
    logger.info("Saved carbon display (%s gCO2/kWh, %s) to %s",
                value_str, verdict, output_path)
    return output_path


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import yaml

    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yml")
    with open(config_path, "r") as f:
        cfg = yaml.safe_load(f)

    result = generate(cfg)
    print(f"Generated: {result}")
