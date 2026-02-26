"""
Downtown Franklin Parking Garage module.

Fetches real-time occupancy data from the Franklin, TN Indect parking API,
renders a two-column display with circular progress gauges and per-level
breakdown bars, saves historical data to parquet for busyness prediction.
"""

import math
import os
import platform
import requests
import urllib3
from datetime import datetime

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from PIL import Image, ImageDraw, ImageFont

try:
    import pandas as pd
except ImportError:
    pd = None


HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EinkDisplay/1.0)"}
API_URL = "https://apps.franklintn.gov/indect/structure"


def _font_path():
    if platform.system() == "Darwin":
        return "/Library/Fonts/Arial Unicode.ttf"
    return "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"


def _font(size):
    try:
        return ImageFont.truetype(_font_path(), size)
    except Exception:
        return ImageFont.load_default()


def _fetch_data(api_url):
    """Fetch parking structure JSON. Returns parsed dict or None."""
    try:
        resp = requests.get(api_url, headers=HEADERS, timeout=15, verify=False)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[parking] Failed to fetch data: {e}")
        return None


def _parse_garages(data):
    """Parse the API response into a list of garage dicts."""
    garages = []
    for zone in data.get("Zones", []):
        name = zone.get("Name", "Unknown")
        total = zone.get("TotalBays", 0)
        occupied = zone.get("OccupiedBays", 0)
        levels = []
        for sub in zone.get("Zones", []):
            level_name = sub.get("Name", "")
            # Only include numbered levels (skip ADA, EV, Reserved, Timed)
            if not level_name.startswith("Level") or not any(c.isdigit() for c in level_name):
                continue
            # Skip special sub-levels (ADA, EV, timed parking)
            if any(s in level_name for s in ("ADA", "EV", "Hour", "Timed")):
                continue
            levels.append({
                "name": level_name,
                "total": sub.get("TotalBays", 0),
                "occupied": sub.get("OccupiedBays", 0),
            })
        garages.append({
            "name": name,
            "total": total,
            "occupied": occupied,
            "available": max(0, total - occupied),
            "levels": levels,
        })
    return garages


def _save_history(garages, history_file):
    """Append current occupancy to parquet history file."""
    if pd is None:
        print("[parking] pandas not available, skipping history save")
        return
    now = datetime.now()
    rows = []
    for g in garages:
        for level in g["levels"]:
            rows.append({
                "timestamp": now,
                "garage_name": g["name"],
                "level": level["name"],
                "total_bays": level["total"],
                "occupied_bays": level["occupied"],
            })
        # Also store garage totals
        rows.append({
            "timestamp": now,
            "garage_name": g["name"],
            "level": "TOTAL",
            "total_bays": g["total"],
            "occupied_bays": g["occupied"],
        })

    new_df = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(history_file) or ".", exist_ok=True)

    if os.path.exists(history_file):
        try:
            existing = pd.read_parquet(history_file)
            combined = pd.concat([existing, new_df], ignore_index=True)
        except Exception:
            combined = new_df
    else:
        combined = new_df

    combined.to_parquet(history_file, index=False)
    print(f"[parking] Saved history ({len(combined)} rows) to {history_file}")


def _get_prediction(history_file, garage_name):
    """Get predicted occupancy % for this day-of-week + hour from history."""
    if pd is None or not os.path.exists(history_file):
        return None
    try:
        df = pd.read_parquet(history_file)
        df = df[df["level"] == "TOTAL"]
        df = df[df["garage_name"] == garage_name]
        if len(df) < 5:
            return None
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["dow"] = df["timestamp"].dt.dayofweek
        df["hour"] = df["timestamp"].dt.hour
        now = datetime.now()
        mask = (df["dow"] == now.weekday()) & (df["hour"] == now.hour)
        subset = df[mask]
        if len(subset) < 2:
            return None
        avg_pct = (subset["occupied_bays"] / subset["total_bays"]).mean()
        return avg_pct
    except Exception:
        return None


def _draw_arc_gauge(draw, cx, cy, radius, pct, color, bg_color="#e0e0e0"):
    """Draw a circular progress arc gauge."""
    bbox = [cx - radius, cy - radius, cx + radius, cy + radius]
    # Background circle
    draw.arc(bbox, 0, 360, fill=bg_color, width=12)
    # Filled arc (start from top = -90 degrees)
    if pct > 0:
        end_angle = -90 + int(360 * min(pct, 1.0))
        draw.arc(bbox, -90, end_angle, fill=color, width=12)


def _draw_level_bar(draw, x, y, width, height, pct, color):
    """Draw a horizontal bar showing occupancy for one level."""
    # Background
    draw.rectangle([x, y, x + width, y + height], fill="#e0e0e0")
    # Filled portion
    filled_w = int(width * min(pct, 1.0))
    if filled_w > 0:
        draw.rectangle([x, y, x + filled_w, y + height], fill=color)
    # Border
    draw.rectangle([x, y, x + width, y + height], outline="#999999", width=1)


def _pct_color(pct):
    """Return color based on occupancy percentage."""
    if pct < 0.5:
        return "#2ecc71"  # green
    elif pct < 0.75:
        return "#f39c12"  # orange
    else:
        return "#e74c3c"  # red


def _render(garages, total_data, predictions, output_path, width=800, height=480):
    """Render the parking display."""
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    # Header
    header_font = _font(28)
    header_text = "Downtown Franklin Parking"
    hbox = draw.textbbox((0, 0), header_text, font=header_font)
    hx = (width - (hbox[2] - hbox[0])) // 2
    draw.text((hx, 12), header_text, fill="black", font=header_font)

    # Timestamp
    ts_font = _font(14)
    ts_text = datetime.now().strftime("%b %d, %Y  %I:%M %p")
    ts_box = draw.textbbox((0, 0), ts_text, font=ts_font)
    draw.text((width - (ts_box[2] - ts_box[0]) - 10, 18), ts_text,
              fill="#888888", font=ts_font)

    # Divider
    draw.line([(20, 50), (width - 20, 50)], fill="#cccccc", width=1)

    # Two-column layout
    col_width = width // 2
    top_y = 60

    for col_idx, garage in enumerate(garages[:2]):
        x_offset = col_idx * col_width
        cx = x_offset + col_width // 2

        pct = garage["occupied"] / garage["total"] if garage["total"] > 0 else 0
        color = _pct_color(pct)

        # Garage name
        name_font = _font(18)
        name = garage["name"]
        nbox = draw.textbbox((0, 0), name, font=name_font)
        nx = cx - (nbox[2] - nbox[0]) // 2
        draw.text((nx, top_y), name, fill="black", font=name_font)

        # Circular gauge
        gauge_cy = top_y + 110
        gauge_r = 55
        _draw_arc_gauge(draw, cx, gauge_cy, gauge_r, pct, color)

        # Percentage text inside gauge
        pct_font = _font(28)
        pct_text = f"{int(pct * 100)}%"
        pbox = draw.textbbox((0, 0), pct_text, font=pct_font)
        draw.text((cx - (pbox[2] - pbox[0]) // 2, gauge_cy - 18),
                  pct_text, fill=color, font=pct_font)

        # "full" label
        full_font = _font(12)
        draw.text((cx - 10, gauge_cy + 12), "full", fill="#888888", font=full_font)

        # Available spots
        avail_font = _font(16)
        avail_text = f"{garage['available']} available"
        abox = draw.textbbox((0, 0), avail_text, font=avail_font)
        draw.text((cx - (abox[2] - abox[0]) // 2, gauge_cy + gauge_r + 10),
                  avail_text, fill="#444444", font=avail_font)

        # Prediction
        pred = predictions.get(garage["name"])
        if pred is not None:
            pred_font = _font(12)
            pred_text = f"Typical: {int(pred * 100)}% full"
            pbox = draw.textbbox((0, 0), pred_text, font=pred_font)
            draw.text((cx - (pbox[2] - pbox[0]) // 2, gauge_cy + gauge_r + 30),
                      pred_text, fill="#aaaaaa", font=pred_font)

        # Per-level bars
        bar_top = top_y + 230
        bar_w = col_width - 60
        bar_h = 16
        bar_x = x_offset + 30
        level_font = _font(12)

        for i, level in enumerate(garage["levels"]):
            by = bar_top + i * (bar_h + 8)
            if by + bar_h > height - 60:
                break
            lpct = level["occupied"] / level["total"] if level["total"] > 0 else 0
            lcolor = _pct_color(lpct)

            # Level label
            draw.text((bar_x, by), level["name"], fill="#444444", font=level_font)
            label_w = draw.textbbox((0, 0), level["name"], font=level_font)[2] + 5

            _draw_level_bar(draw, bar_x + label_w + 5, by + 1, bar_w - label_w - 40, bar_h - 2, lpct, lcolor)

            # Count text
            count_text = f"{level['occupied']}/{level['total']}"
            cbox = draw.textbbox((0, 0), count_text, font=level_font)
            draw.text((bar_x + bar_w - (cbox[2] - cbox[0]), by),
                      count_text, fill="#666666", font=level_font)

    # Vertical divider between columns
    draw.line([(col_width, 55), (col_width, height - 50)], fill="#dddddd", width=1)

    # Bottom combined total
    total_total = total_data.get("TotalBays", 0)
    total_occupied = total_data.get("OccupiedBays", 0)
    total_avail = max(0, total_total - total_occupied)
    draw.line([(20, height - 45), (width - 20, height - 45)], fill="#cccccc", width=1)

    total_font = _font(18)
    total_text = f"Combined: {total_avail} of {total_total} spots available"
    tbox = draw.textbbox((0, 0), total_text, font=total_font)
    draw.text(((width - (tbox[2] - tbox[0])) // 2, height - 35),
              total_text, fill="black", font=total_font)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path)
    print(f"[parking] Saved display to {output_path}")
    return output_path


def _error_image(output_path, width=800, height=480, message="Parking data unavailable."):
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    font = _font(28)
    draw.text((40, height // 2 - 20), message, fill="black", font=font)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path)
    return output_path


def generate(config):
    """Generate Downtown Franklin parking display. Return output path."""
    park_cfg = config.get("parking_garage", {})
    output_path = park_cfg.get("output_path", "images/parking_display.bmp")
    api_url = park_cfg.get("api_url", API_URL)
    history_file = park_cfg.get("history_file", "data/parking_history.parquet")
    width = config.get("width", 800)
    height = config.get("height", 480)

    print("[parking] Fetching parking data...")
    data = _fetch_data(api_url)
    if data is None:
        return _error_image(output_path, width, height)

    garages = _parse_garages(data)
    if not garages:
        return _error_image(output_path, width, height, "No garage data found.")

    # Save history
    _save_history(garages, history_file)

    # Get predictions
    predictions = {}
    for g in garages:
        pred = _get_prediction(history_file, g["name"])
        if pred is not None:
            predictions[g["name"]] = pred

    total_data = {
        "TotalBays": data.get("TotalBays", 0),
        "OccupiedBays": data.get("OccupiedBays", 0),
    }

    return _render(garages, total_data, predictions, output_path, width, height)


if __name__ == "__main__":
    import yaml
    with open("config.yml") as f:
        cfg = yaml.safe_load(f)
    path = generate(cfg)
    print(f"Output: {path}")
