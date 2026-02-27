"""
On This Day module.

Fetches historical events that happened on today's date from the Wikipedia
REST API and renders them as an 800x480 BMP.

Layout:
  - Header: "On This Day" (large, black)
  - Subheader: date like "February 19" (gray)
  - Horizontal rule
  - Up to 6 events as bullets: "• YEAR — event text"
"""

import os
import json
import platform
import requests
from datetime import date
from PIL import Image, ImageDraw, ImageFont


HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EinkDisplay/1.0)"}
CACHE_DIR = "data"


def _font_path():
    if platform.system() == "Darwin":
        return "/Library/Fonts/Arial Unicode.ttf"
    return "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"


def _cache_path():
    today = date.today().isoformat()
    return os.path.join(CACHE_DIR, f"onthisday_cache_{today}.json")


def _load_cache():
    path = _cache_path()
    if os.path.exists(path):
        print(f"[onthisday] Loading cached data from {path}")
        with open(path) as f:
            return json.load(f)
    return None


def _save_cache(data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(), "w") as f:
        json.dump(data, f)
    print(f"[onthisday] Cache saved to {_cache_path()}")


def _wrap_text(draw, text, font, max_width):
    words = text.split()
    lines = []
    current = []
    for word in words:
        test = " ".join(current + [word])
        w = draw.textbbox((0, 0), test, font=font)[2]
        if w <= max_width:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


def _fetch_events():
    today = date.today()
    mm = today.strftime("%m")
    dd = today.strftime("%d")
    url = f"https://en.wikipedia.org/api/rest_v1/feed/onthisday/events/{mm}/{dd}"
    print(f"[onthisday] Fetching {url}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("events", [])
    except Exception as e:
        print(f"[onthisday] Failed to fetch events: {e}")
        return None


def _select_events(events, max_count=6):
    if not events:
        return []
    short_events = [e for e in events if len(e.get("text", "")) < 120]
    pool = short_events if short_events else events
    pool = sorted(pool, key=lambda e: e.get("year", 0))
    return pool[:max_count]


def _render(events, output_path, width=800, height=480):
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    fp = _font_path()
    margin = 20
    text_w = width - 2 * margin
    y = margin

    try:
        header_font = ImageFont.truetype(fp, 40)
    except Exception:
        header_font = ImageFont.load_default()
    draw.text((margin, y), "On This Day", fill="black", font=header_font)
    y += draw.textbbox((0, 0), "On This Day", font=header_font)[3] + 6

    today_label = date.today().strftime("%B %-d")
    try:
        sub_font = ImageFont.truetype(fp, 24)
    except Exception:
        sub_font = ImageFont.load_default()
    draw.text((margin, y), today_label, fill=(100, 100, 100), font=sub_font)
    y += draw.textbbox((0, 0), today_label, font=sub_font)[3] + 8

    draw.line([(margin, y), (width - margin, y)], fill=(180, 180, 180), width=1)
    y += 10

    if not events:
        try:
            msg_font = ImageFont.truetype(fp, 20)
        except Exception:
            msg_font = ImageFont.load_default()
        draw.text((margin, y), "Could not fetch historical events.", fill="black", font=msg_font)
        img.save(output_path)
        print(f"[onthisday] Saved to {output_path}")
        return output_path

    remaining_h = height - y - margin
    bullets = [f"\u2022 {e['year']} \u2014 {e['text']}" for e in events]

    chosen_font = None
    chosen_lines_per_bullet = None
    for size in range(20, 10, -1):
        try:
            font = ImageFont.truetype(fp, size)
        except Exception:
            font = ImageFont.load_default()
        line_h = draw.textbbox((0, 0), "Ag", font=font)[3] + 3
        all_wrapped = [_wrap_text(draw, b, font, text_w) for b in bullets]
        total_lines = sum(len(wrapped) for wrapped in all_wrapped)
        total_h = total_lines * line_h + (len(bullets) - 1) * 4
        if total_h <= remaining_h:
            chosen_font = font
            chosen_lines_per_bullet = all_wrapped
            break

    if chosen_font is None:
        try:
            chosen_font = ImageFont.truetype(fp, 11)
        except Exception:
            chosen_font = ImageFont.load_default()
        chosen_lines_per_bullet = [_wrap_text(draw, b, chosen_font, text_w) for b in bullets]

    line_h = draw.textbbox((0, 0), "Ag", font=chosen_font)[3] + 3

    for wrapped_lines in chosen_lines_per_bullet:
        for line in wrapped_lines:
            if y + line_h > height - margin:
                break
            draw.text((margin, y), line, fill="black", font=chosen_font)
            y += line_h
        y += 4
        if y > height - margin:
            break

    img.save(output_path)
    print(f"[onthisday] Saved to {output_path}")
    return output_path


def generate(config):
    """Generate On This Day image. Return output path."""
    otd_cfg = config.get("on_this_day", {})
    output_path = otd_cfg.get("output_path", "onthisday_display.bmp")

    raw_events = _load_cache()
    if raw_events is None:
        raw_events = _fetch_events()
        if raw_events is not None:
            _save_cache(raw_events)

    if raw_events is None:
        print("[onthisday] Rendering fallback image.")
        return _render([], output_path)

    selected = _select_events(raw_events)
    print(f"[onthisday] Selected {len(selected)} events to display.")
    return _render(selected, output_path)


if __name__ == "__main__":
    import yaml
    with open("config.yml") as f:
        cfg = yaml.safe_load(f)
    path = generate(cfg)
    print(f"Output: {path}")
