"""
48-Hour Weather Forecast Graph for a Waveshare e-ink display (800x480, BMP).

Plots an hourly temperature line with precipitation-probability bars behind it,
using the keyless Open-Meteo forecast API. The response is cached for 30 minutes
and reused (even when stale) if the network fetch fails.

Config block (config.yml):

    forecast_graph:
      output_path: images/forecast_graph.bmp
      cache_dir: data/

    forecast_location:
      latitude: 35.9251
      longitude: -86.8689
      name: "Franklin, TN"
"""

import os
import sys
import json
import time
from datetime import datetime

import requests
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import get_font, get_logger

logger = get_logger("forecast_graph")

# ── Constants ────────────────────────────────────────────────────────────────
WIDTH, HEIGHT = 800, 480
HOURS = 48
CACHE_TTL = 30 * 60  # 30 minutes

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
GRAY = (128, 128, 128)
LIGHT_GRAY = (200, 200, 200)
BLUE = (0, 0, 255)          # one of the 7 e-ink colors
LIGHT_BLUE = (150, 180, 255)  # precip bars (dithers toward blue on the panel)

API_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude={lat}&longitude={lon}"
    "&hourly=temperature_2m,precipitation_probability,precipitation,weather_code"
    "&temperature_unit=fahrenheit&precipitation_unit=inch"
    "&timezone=auto&forecast_days=3"
)


# ── Data ─────────────────────────────────────────────────────────────────────
def _cache_path(config):
    cache_dir = config.get("forecast_graph", {}).get("cache_dir", "data/")
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, "forecast_graph_cache.json")


def _read_cache(path):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return None


def fetch_forecast(config):
    """Return the Open-Meteo response dict, using a 30-min cache.

    Falls back to stale cache on any network/parse failure. Returns None only
    when there is no fresh data and no cache at all.
    """
    loc = config.get("forecast_location", {})
    lat = loc.get("latitude")
    lon = loc.get("longitude")
    path = _cache_path(config)

    cached = _read_cache(path)
    if cached and (time.time() - cached.get("_fetched_at", 0) < CACHE_TTL):
        logger.info("Using fresh cached forecast")
        return cached

    if lat is None or lon is None:
        logger.error("No forecast_location coordinates in config")
        return cached  # may be None

    url = API_URL.format(lat=lat, lon=lon)
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        data["_fetched_at"] = time.time()
        try:
            with open(path, "w") as f:
                json.dump(data, f)
        except Exception as e:
            logger.warning("Could not write cache: %s", e)
        logger.info("Fetched fresh forecast from Open-Meteo")
        return data
    except Exception as e:
        logger.error("Forecast fetch failed: %s", e)
        if cached:
            logger.info("Falling back to stale cached forecast")
        return cached  # may be None


def _slice_hours(data):
    """Return (times, temps, precip_prob) for the first HOURS entries."""
    hourly = data.get("hourly", {})
    times = hourly.get("time", []) or []
    temps = hourly.get("temperature_2m", []) or []
    probs = hourly.get("precipitation_probability", []) or []

    n = min(len(times), len(temps), HOURS)
    if n == 0:
        return None

    times = times[:n]
    temps = temps[:n]
    probs = (probs + [0] * n)[:n]
    # Replace any None values so plotting math is safe.
    temps = [t if t is not None else 0 for t in temps]
    probs = [p if p is not None else 0 for p in probs]
    return times, temps, probs


# ── Rendering ────────────────────────────────────────────────────────────────
def _dashed_hline(draw, x0, x1, y, fill, dash=6, gap=5):
    x = x0
    while x < x1:
        draw.line([(x, y), (min(x + dash, x1), y)], fill=fill, width=1)
        x += dash + gap


def _render_unavailable(output_path):
    img = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(img)
    font = get_font(36, bold=True)
    msg = "Forecast unavailable"
    bbox = draw.textbbox((0, 0), msg, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    draw.text(((WIDTH - w) / 2, (HEIGHT - h) / 2), msg, fill=BLACK, font=font)
    img.save(output_path)
    logger.warning("Rendered 'Forecast unavailable'")
    return output_path


def render_graph(config, data, output_path):
    sliced = _slice_hours(data)
    if not sliced:
        return _render_unavailable(output_path)

    times, temps, probs = sliced
    n = len(times)

    location_name = config.get("forecast_location", {}).get("name", "")

    img = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(img)

    # ── Fonts ────────────────────────────────────────────────────────────────
    title_font = get_font(22, bold=True, config=config)
    loc_font = get_font(16, config=config)
    small_font = get_font(13, config=config)
    label_font = get_font(14, bold=True, config=config)

    # ── Header ───────────────────────────────────────────────────────────────
    draw.text((14, 12), "48-Hour Forecast", fill=BLACK, font=title_font)
    tw = draw.textbbox((0, 0), "48-Hour Forecast", font=title_font)[2]
    if location_name:
        draw.text((14 + tw + 12, 18), location_name, fill=GRAY, font=loc_font)

    now_str = datetime.now().strftime("%a %b %-d, %-I:%M %p")
    nw = draw.textbbox((0, 0), now_str, font=loc_font)[2]
    draw.text((WIDTH - nw - 14, 18), now_str, fill=GRAY, font=loc_font)

    # ── Chart geometry ───────────────────────────────────────────────────────
    left = 50
    right = WIDTH - 50
    top = 60
    bottom = HEIGHT - 40
    plot_w = right - left
    plot_h = bottom - top

    def x_at(i):
        if n <= 1:
            return left
        return left + plot_w * i / (n - 1)

    # ── Temperature Y scale ──────────────────────────────────────────────────
    tmin = min(temps)
    tmax = max(temps)
    y_lo = tmin - 2
    y_hi = tmax + 2
    if y_hi - y_lo < 1:
        y_hi = y_lo + 1

    def y_at(temp):
        return bottom - plot_h * (temp - y_lo) / (y_hi - y_lo)

    # ── Temp gridlines (dashed gray) + °F labels ─────────────────────────────
    n_grid = 5
    for g in range(n_grid + 1):
        val = y_lo + (y_hi - y_lo) * g / n_grid
        gy = y_at(val)
        _dashed_hline(draw, left, right, int(gy), LIGHT_GRAY)
        lbl = f"{round(val)}°"
        lb = draw.textbbox((0, 0), lbl, font=small_font)
        draw.text((left - (lb[2] - lb[0]) - 6, gy - (lb[3] - lb[1]) / 2 - 2),
                  lbl, fill=GRAY, font=small_font)

    # ── Precip probability bars (behind temp line) ───────────────────────────
    if n > 1:
        bar_w = max(2, int(plot_w / n) - 1)
    else:
        bar_w = 6
    for i in range(n):
        p = probs[i]
        if p <= 0:
            continue
        bh = plot_h * (p / 100.0)
        cx = x_at(i)
        x0 = cx - bar_w / 2
        x1 = cx + bar_w / 2
        draw.rectangle([x0, bottom - bh, x1, bottom], fill=LIGHT_BLUE)

    # ── X-axis: gridlines, midnight markers, time labels ─────────────────────
    def parse_hour(t):
        try:
            return datetime.strptime(t, "%Y-%m-%dT%H:%M")
        except Exception:
            return None

    for i in range(n):
        dt = parse_hour(times[i])
        if dt is None:
            continue
        cx = x_at(i)
        is_midnight = dt.hour == 0
        if is_midnight:
            draw.line([(cx, top), (cx, bottom)], fill=GRAY, width=1)
            wd = dt.strftime("%a")
            wb = draw.textbbox((0, 0), wd, font=label_font)
            draw.text((cx - (wb[2] - wb[0]) / 2, top - 2), wd, fill=BLACK, font=label_font)
            lbl = "12AM"
        elif dt.hour % 6 == 0:
            draw.line([(cx, top), (cx, bottom)], fill=LIGHT_GRAY, width=1)
            hour12 = dt.hour % 12 or 12
            ampm = "AM" if dt.hour < 12 else "PM"
            lbl = f"{hour12}{ampm}"
        else:
            continue

        tb = draw.textbbox((0, 0), lbl, font=small_font)
        draw.text((cx - (tb[2] - tb[0]) / 2, bottom + 6), lbl, fill=GRAY, font=small_font)

    # ── Axis border ──────────────────────────────────────────────────────────
    draw.line([(left, top), (left, bottom)], fill=GRAY, width=1)
    draw.line([(left, bottom), (right, bottom)], fill=GRAY, width=1)

    # ── Temperature line (on top) ────────────────────────────────────────────
    pts = [(x_at(i), y_at(temps[i])) for i in range(n)]
    if len(pts) >= 2:
        draw.line(pts, fill=BLACK, width=3, joint="curve")
    elif pts:
        draw.ellipse([pts[0][0] - 2, pts[0][1] - 2, pts[0][0] + 2, pts[0][1] + 2], fill=BLACK)

    # ── High / low markers ───────────────────────────────────────────────────
    hi_i = temps.index(tmax)
    lo_i = temps.index(tmin)

    def mark(i, temp, above):
        px, py = x_at(i), y_at(temp)
        r = 4
        draw.ellipse([px - r, py - r, px + r, py + r], fill=BLACK)
        lbl = f"{round(temp)}°F"
        lb = draw.textbbox((0, 0), lbl, font=label_font)
        lw = lb[2] - lb[0]
        lh = lb[3] - lb[1]
        ly = py - r - lh - 4 if above else py + r + 4
        lx = min(max(px - lw / 2, left), right - lw)
        draw.text((lx, ly), lbl, fill=BLACK, font=label_font)

    mark(hi_i, tmax, above=True)
    mark(lo_i, tmin, above=False)

    # ── Legend (bottom-right) ────────────────────────────────────────────────
    ly = HEIGHT - 20
    lx = WIDTH - 200
    draw.line([(lx, ly + 7), (lx + 20, ly + 7)], fill=BLACK, width=3)
    draw.text((lx + 26, ly), "Temp °F", fill=BLACK, font=small_font)
    lx2 = lx + 100
    draw.rectangle([lx2, ly + 2, lx2 + 12, ly + 13], fill=LIGHT_BLUE)
    draw.text((lx2 + 18, ly), "Precip %", fill=BLUE, font=small_font)

    img.save(output_path)
    logger.info("Saved forecast graph to %s", output_path)
    return output_path


# ── Module interface ─────────────────────────────────────────────────────────
def generate(config):
    """Generate the 48-hour forecast graph. Returns the output BMP path."""
    output_path = config.get("forecast_graph", {}).get(
        "output_path", "images/forecast_graph.bmp"
    )
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    data = fetch_forecast(config)
    if not data:
        return _render_unavailable(output_path)

    try:
        return render_graph(config, data, output_path)
    except Exception as e:
        logger.exception("Failed to render forecast graph: %s", e)
        return _render_unavailable(output_path)


if __name__ == "__main__":
    import yaml

    with open("config.yml", "r") as f:
        cfg = yaml.safe_load(f)
    result = generate(cfg)
    print(f"Output: {result}")
