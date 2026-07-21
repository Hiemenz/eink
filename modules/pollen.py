"""
Pollen Count Display Module — E-ink display showing current pollen grain
counts (grains/m³) for six common allergenic species, sourced from the
KEYLESS Open-Meteo Air Quality API.

800×480 pixels, output as BMP.

Config section (add to config.yml):

    pollen:
      output_path: images/pollen_display.bmp
      cache_dir: data/

Shared config used:

    forecast_location:
      latitude: 35.9251
      longitude: -86.8689
      name: "Franklin, TN"

Data source (no API key required):
    https://air-quality-api.open-meteo.com/v1/air-quality
        ?latitude={lat}&longitude={lon}
        &hourly=alder_pollen,birch_pollen,grass_pollen,mugwort_pollen,
                olive_pollen,ragweed_pollen
        &timezone=auto&forecast_days=1

Values are grains/m³ (floats, may be null). NOTE: Open-Meteo pollen data
covers Europe well; for North America most/all species return null — this
is handled gracefully (cards show "—"/"n/a" with an explanatory note).

Severity mapping (grains/m³ → level, e-ink colors):
    0            None       → White  (255, 255, 255)
    < 10         Low        → Green  (0,   255,   0)
    10 – 50      Moderate   → Yellow (255, 255,   0)
    50 – 500     High       → Orange (255, 128,   0)
    > 500        Very High  → Red    (255,   0,   0)
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

logger = get_logger("pollen")

# ── Constants ────────────────────────────────────────────────────────────────

WIDTH = 800
HEIGHT = 480
CACHE_TTL = 3600  # 1 hour in seconds

GRAY = (120, 120, 120)
LIGHT_GRAY = (190, 190, 190)
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)

API_URL = (
    "https://air-quality-api.open-meteo.com/v1/air-quality"
    "?latitude={lat}&longitude={lon}"
    "&hourly=alder_pollen,birch_pollen,grass_pollen,mugwort_pollen,"
    "olive_pollen,ragweed_pollen"
    "&timezone=auto&forecast_days=1"
)

# (api_key, display_name)
SPECIES = [
    ("alder_pollen",   "Alder"),
    ("birch_pollen",   "Birch"),
    ("grass_pollen",   "Grass"),
    ("mugwort_pollen", "Mugwort"),
    ("olive_pollen",   "Olive"),
    ("ragweed_pollen", "Ragweed"),
]

# Severity tiers: (label, rgb_color, text_on_color)
# Selected by _severity(value) using the thresholds below.
SEV_NONE      = ("None",      (255, 255, 255), BLACK)
SEV_LOW       = ("Low",       (0,   255,   0), BLACK)
SEV_MODERATE  = ("Moderate",  (255, 255,   0), BLACK)
SEV_HIGH      = ("High",      (255, 128,   0), BLACK)
SEV_VERY_HIGH = ("Very High", (255,   0,   0), WHITE)

# Ordered lowest → highest severity, for legend and max-verdict comparison.
SEV_ORDER = [SEV_NONE, SEV_LOW, SEV_MODERATE, SEV_HIGH, SEV_VERY_HIGH]


# ── Severity helpers ─────────────────────────────────────────────────────────

def _severity(value: Optional[float]) -> Optional[tuple]:
    """Map a grains/m³ value to a severity tier tuple.

    Returns None when the value is null/unknown (no reading available).
    """
    if value is None:
        return None
    if value <= 0:
        return SEV_NONE
    if value < 10:
        return SEV_LOW
    if value < 50:
        return SEV_MODERATE
    if value <= 500:
        return SEV_HIGH
    return SEV_VERY_HIGH


def _severity_rank(tier: tuple) -> int:
    """Return the ordinal rank of a severity tier (0 = None … 4 = Very High)."""
    return SEV_ORDER.index(tier)


# ── Cache ─────────────────────────────────────────────────────────────────────

def _cache_path(cache_dir: str) -> str:
    return os.path.join(cache_dir, "pollen_cache.json")


def _load_cache(cache_dir: str) -> Optional[dict]:
    """Load cached pollen data if present. Returns dict or None."""
    try:
        with open(_cache_path(cache_dir), "r") as f:
            return json.load(f)
    except Exception:
        return None


def _save_cache(cache_dir: str, payload: dict) -> None:
    """Write pollen payload to cache file."""
    os.makedirs(cache_dir, exist_ok=True)
    try:
        with open(_cache_path(cache_dir), "w") as f:
            json.dump(payload, f)
    except Exception as e:
        logger.warning("Could not write pollen cache: %s", e)


# ── Data fetch ───────────────────────────────────────────────────────────────

def _current_hour_index(times: list) -> int:
    """Return the hourly index matching the current local hour.

    Falls back to index 0 if no match is found.
    """
    now_key = datetime.now().strftime("%Y-%m-%dT%H:00")
    for i, t in enumerate(times or []):
        if t.startswith(now_key):
            return i
    return 0


def _fetch_pollen(lat: float, lon: float) -> Optional[dict]:
    """Fetch current-hour pollen readings from Open-Meteo.

    Returns dict {species_key: value_or_None, ..., "fetched_at": ts} or
    None on HTTP failure.
    """
    url = API_URL.format(lat=lat, lon=lon)
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        logger.error("Open-Meteo pollen fetch failed: %s", e)
        return None

    hourly = payload.get("hourly", {})
    times = hourly.get("time", [])
    idx = _current_hour_index(times)

    result: dict = {"fetched_at": time.time()}
    for key, _name in SPECIES:
        series = hourly.get(key) or []
        value = None
        if idx < len(series):
            value = series[idx]
        # Simple fallback: first non-null reading of the day.
        if value is None:
            value = next((v for v in series if v is not None), None)
        result[key] = value

    return result


def _get_pollen_data(lat: float, lon: float, cache_dir: str) -> Optional[dict]:
    """Return pollen data using a 1-hour cache; stale cache on failure.

    Returns None only when the HTTP request fails AND no cache exists.
    """
    os.makedirs(cache_dir, exist_ok=True)
    cache = _load_cache(cache_dir)

    # Fresh cache still valid
    if cache and (time.time() - cache.get("fetched_at", 0)) < CACHE_TTL:
        logger.info("Using cached pollen data (age: %.0fs)",
                    time.time() - cache["fetched_at"])
        return cache

    # Live fetch
    data = _fetch_pollen(lat, lon)
    if data:
        _save_cache(cache_dir, data)
        logger.info("Fetched pollen data for (%.4f, %.4f)", lat, lon)
        return data

    logger.warning("Live pollen fetch failed — trying stale cache")
    if cache:
        age_min = (time.time() - cache.get("fetched_at", 0)) / 60
        logger.warning("Using stale pollen cache (age: %.0f min)", age_min)
        return cache

    return None


# ── Drawing helpers ───────────────────────────────────────────────────────────

def _center_text(draw, cx, y, text, font, fill):
    """Draw text horizontally centered on cx at vertical position y."""
    bb = draw.textbbox((0, 0), text, font=font)
    draw.text((cx - (bb[2] - bb[0]) // 2, y), text, fill=fill, font=font)


def _fmt_value(value: Optional[float]) -> str:
    """Format a grains/m³ value for display."""
    if value is None:
        return "—"
    if value == 0:
        return "0"
    if value < 10:
        return f"{value:.1f}"
    return f"{value:.0f}"


def _draw_failure(img: Image.Image, config: dict) -> None:
    """Draw the hard-failure screen (HTTP failed and no cache)."""
    draw = ImageDraw.Draw(img)
    font = get_font(38, bold=True, config=config)
    msg = "Pollen data unavailable"
    bb = draw.textbbox((0, 0), msg, font=font)
    x = (WIDTH - (bb[2] - bb[0])) // 2
    y = (HEIGHT - (bb[3] - bb[1])) // 2
    draw.text((x, y), msg, fill=GRAY, font=font)

    sub = get_font(16, config=config)
    hint = "Could not reach Open-Meteo and no cached reading exists"
    hbb = draw.textbbox((0, 0), hint, font=sub)
    hx = (WIDTH - (hbb[2] - hbb[0])) // 2
    draw.text((hx, y + (bb[3] - bb[1]) + 14), hint, fill=LIGHT_GRAY, font=sub)


def _draw_card(draw, x, y, w, h, name, value, config):
    """Draw a single species card with name, value, and severity dot/bar."""
    tier = _severity(value)

    # Card border
    draw.rectangle([(x, y), (x + w - 1, y + h - 1)], outline=LIGHT_GRAY, width=1)

    # Severity color bar across the top of the card
    bar_h = 6
    bar_color = tier[1] if tier else WHITE
    draw.rectangle([(x + 1, y + 1), (x + w - 2, y + bar_h)], fill=bar_color)
    if tier is SEV_NONE or tier is None:
        # keep the (near-white) bar visible with a faint outline
        draw.rectangle([(x + 1, y + 1), (x + w - 2, y + bar_h)],
                       outline=LIGHT_GRAY, width=1)

    cx = x + w // 2

    # Species name
    name_font = get_font(20, bold=True, config=config)
    _center_text(draw, cx, y + 16, name, name_font, BLACK)

    # Value (grains/m³) — colored by severity, gray when null
    val_font = get_font(40, bold=True, config=config)
    val_str = _fmt_value(value)
    val_color = tier[1] if (tier and tier is not SEV_NONE) else GRAY
    if tier is SEV_NONE:
        val_color = BLACK
    vbb = draw.textbbox((0, 0), val_str, font=val_font)
    _center_text(draw, cx, y + 44, val_str, val_font, val_color)

    # Severity dot + label
    label = tier[0] if tier else "n/a"
    dot_font = get_font(15, config=config)
    lbb = draw.textbbox((0, 0), label, font=dot_font)
    lbl_w = lbb[2] - lbb[0]
    dot_r = 6
    group_w = dot_r * 2 + 6 + lbl_w
    gx = cx - group_w // 2
    dot_cy = y + h - 20
    dot_color = tier[1] if tier else WHITE
    draw.ellipse([(gx, dot_cy - dot_r), (gx + dot_r * 2, dot_cy + dot_r)],
                 fill=dot_color, outline=LIGHT_GRAY, width=1)
    draw.text((gx + dot_r * 2 + 6, dot_cy - (lbb[3] - lbb[1]) // 2 - lbb[1]),
              label, fill=GRAY, font=dot_font)


def _draw_legend(draw, x, y, w, config):
    """Draw the 5-tier severity legend as a horizontal row of swatches."""
    font = get_font(13, config=config)
    swatch = 14
    gap = 6

    # Measure total width to center the legend within [x, x+w]
    items = []
    total = 0
    for label, color, _txt in SEV_ORDER:
        lbb = draw.textbbox((0, 0), label, font=font)
        seg_w = swatch + 5 + (lbb[2] - lbb[0])
        items.append((label, color, seg_w, lbb))
        total += seg_w
    total += gap * (len(items) - 1) * 5  # spacing between items

    spacing = 30
    total = sum(it[2] for it in items) + spacing * (len(items) - 1)
    cur = x + (w - total) // 2

    for label, color, seg_w, lbb in items:
        outline = LIGHT_GRAY
        draw.rectangle([(cur, y), (cur + swatch, y + swatch)],
                       fill=color, outline=outline, width=1)
        ty = y + (swatch - (lbb[3] - lbb[1])) // 2 - lbb[1]
        draw.text((cur + swatch + 5, ty), label, fill=BLACK, font=font)
        cur += seg_w + spacing


# ── Main generate() ───────────────────────────────────────────────────────────

def generate(config: dict) -> str:
    """Generate the pollen display image and return the output BMP path.

    Reads config section:
        pollen:
          output_path: images/pollen_display.bmp
          cache_dir: data/
    """
    cfg = config.get("pollen", {})
    output_path = cfg.get("output_path", "images/pollen_display.bmp")
    cache_dir = cfg.get("cache_dir", "data/")

    loc = config.get("forecast_location", {})
    lat = loc.get("latitude", 35.9251)
    lon = loc.get("longitude", -86.8689)
    loc_name = loc.get("name", "")

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    os.makedirs(cache_dir, exist_ok=True)

    data = _get_pollen_data(lat, lon, cache_dir)

    img = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(img)

    # Hard failure: request failed AND no cache.
    if data is None:
        _draw_failure(img, config)
        img.save(output_path)
        logger.info("Saved pollen failure screen to %s", output_path)
        return output_path

    # Gather per-species values and severities.
    values = {key: data.get(key) for key, _name in SPECIES}
    tiers = {key: _severity(values[key]) for key, _name in SPECIES}
    non_null = [t for t in tiers.values() if t is not None]
    all_null = len(non_null) == 0

    # Overall verdict = max severity across species.
    if non_null:
        verdict = max(non_null, key=_severity_rank)
    else:
        verdict = None

    fetched_at = data.get("fetched_at", 0)

    # ── Title + location ───────────────────────────────────────────────────
    title_font = get_font(34, bold=True, config=config)
    draw.text((24, 18), "Pollen Count", fill=BLACK, font=title_font)
    if loc_name:
        loc_font = get_font(18, config=config)
        tb = draw.textbbox((0, 0), "Pollen Count", font=title_font)
        draw.text((24, 18 + (tb[3] - tb[1]) + 12), loc_name, fill=GRAY, font=loc_font)

    # ── Verdict badge (top-right) ──────────────────────────────────────────
    badge_label = verdict[0].upper() if verdict else "NO DATA"
    badge_color = verdict[1] if verdict else LIGHT_GRAY
    badge_text_color = verdict[2] if verdict else BLACK
    if verdict is SEV_NONE:
        badge_text_color = BLACK
    badge_font = get_font(30, bold=True, config=config)
    bb = draw.textbbox((0, 0), badge_label, font=badge_font)
    b_w = (bb[2] - bb[0]) + 44
    b_h = (bb[3] - bb[1]) + 26
    b_x1 = WIDTH - 24
    b_x0 = b_x1 - b_w
    b_y0 = 20
    b_y1 = b_y0 + b_h
    draw.rectangle([(b_x0, b_y0), (b_x1, b_y1)], fill=badge_color, outline=BLACK, width=2)
    _center_text(draw, (b_x0 + b_x1) // 2,
                 b_y0 + (b_h - (bb[3] - bb[1])) // 2 - bb[1],
                 badge_label, badge_font, badge_text_color)

    # Small "verdict" caption above the badge
    cap_font = get_font(12, config=config)
    _center_text(draw, (b_x0 + b_x1) // 2, b_y0 - 15, "OVERALL", cap_font, GRAY)

    # ── Species cards grid (2 rows × 3) ────────────────────────────────────
    grid_top = 108
    grid_bottom = HEIGHT - 78   # leave room for legend + footer
    margin_x = 24
    gap = 16
    cols, rows = 3, 2
    card_w = (WIDTH - 2 * margin_x - (cols - 1) * gap) // cols
    card_h = (grid_bottom - grid_top - (rows - 1) * gap) // rows

    for i, (key, name) in enumerate(SPECIES):
        r = i // cols
        c = i % cols
        cx = margin_x + c * (card_w + gap)
        cy = grid_top + r * (card_h + gap)
        _draw_card(draw, cx, cy, card_w, card_h, name, values[key], config)

    # ── "No data for region" note when everything is null ──────────────────
    if all_null:
        note_font = get_font(15, config=config)
        note = "No pollen data for this region"
        _center_text(draw, WIDTH // 2, grid_bottom + 6, note, note_font, GRAY)
        legend_y = grid_bottom + 28
    else:
        legend_y = grid_bottom + 12

    # ── Severity legend ────────────────────────────────────────────────────
    _draw_legend(draw, 0, legend_y, WIDTH, config)

    # ── Footer: update time + attribution (bottom-right, gray) ─────────────
    if fetched_at:
        ts = datetime.fromtimestamp(fetched_at).strftime("%-I:%M %p")
    else:
        ts = datetime.now().strftime("%-I:%M %p")
    footer = f"Updated {ts} · Open-Meteo"
    footer_font = get_font(13, config=config)
    fbb = draw.textbbox((0, 0), footer, font=footer_font)
    draw.text((WIDTH - (fbb[2] - fbb[0]) - 8, HEIGHT - (fbb[3] - fbb[1]) - 8),
              footer, fill=GRAY, font=footer_font)

    img.save(output_path)
    logger.info("Saved pollen display (verdict=%s, all_null=%s) to %s",
                verdict[0] if verdict else "n/a", all_null, output_path)
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
