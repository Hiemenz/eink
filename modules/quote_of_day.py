"""
Quote of the Day module.

Fetches today's quote from zenquotes.io and renders it as an elegant
800x480 BMP with a text-only layout on a white background.

Layout:
  - Top: small "Quote of the Day" header, centered, gray
  - Decorative large opening quotation mark, gray
  - Quote text: auto-sized to fill the canvas, centered, black
  - Thin horizontal rule
  - Bottom: "— Author Name" right-aligned, gray
"""

import os
import json
import platform
import requests
from datetime import date
from PIL import Image, ImageDraw, ImageFont


HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EinkDisplay/1.0)"}
CACHE_DIR = "data"
FALLBACK_QUOTE = "The only way to do great work is to love what you do."
FALLBACK_AUTHOR = "Steve Jobs"


def _font_path():
    if platform.system() == "Darwin":
        return "/Library/Fonts/Arial Unicode.ttf"
    return "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"


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


def _fit_font(draw, text, font_path, max_width, max_height, start_size=72, min_size=14):
    for size in range(start_size, min_size - 1, -2):
        try:
            font = ImageFont.truetype(font_path, size)
        except Exception:
            font = ImageFont.load_default()
        lines = _wrap_text(draw, text, font, max_width)
        line_h = draw.textbbox((0, 0), "Ay", font=font)[3] + 6
        total_h = line_h * len(lines)
        if total_h <= max_height:
            return font, lines
    try:
        font = ImageFont.truetype(font_path, min_size)
    except Exception:
        font = ImageFont.load_default()
    return font, _wrap_text(draw, text, font, max_width)


def _cache_path():
    today = date.today().isoformat()
    return os.path.join(CACHE_DIR, f"quote_cache_{today}.json")


def _load_cache():
    path = _cache_path()
    if os.path.exists(path):
        print(f"[quote] Loading cached quote from {path}")
        with open(path) as f:
            return json.load(f)
    return None


def _save_cache(data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(), "w") as f:
        json.dump(data, f)


def _fetch_quote():
    try:
        resp = requests.get("https://zenquotes.io/api/today", headers=HEADERS, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
        if payload and isinstance(payload, list):
            entry = payload[0]
            q = entry.get("q", "").strip()
            a = entry.get("a", "").strip()
            if q and a:
                print(f"[quote] Fetched quote by {a}")
                return {"q": q, "a": a}
    except Exception as e:
        print(f"[quote] API request failed: {e}")
    return None


def _render(quote_text, author, output_path, width=800, height=480):
    bg = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(bg)
    fp = _font_path()
    margin = 28

    # Header
    header_h = 28
    try:
        header_font = ImageFont.truetype(fp, 14)
    except Exception:
        header_font = ImageFont.load_default()
    header_text = "Quote of the Day"
    hw = draw.textbbox((0, 0), header_text, font=header_font)[2]
    draw.text(((width - hw) // 2, 8), header_text, fill=(160, 160, 160), font=header_font)

    # Decorative opening quote mark
    try:
        deco_font = ImageFont.truetype(fp, 120)
    except Exception:
        deco_font = ImageFont.load_default()
    draw.text((margin, header_h + 4), "\u201C", fill=(210, 210, 210), font=deco_font)

    # Attribution area at bottom
    try:
        attr_font = ImageFont.truetype(fp, 24)
    except Exception:
        attr_font = ImageFont.load_default()
    attr_text = f"\u2014 {author}"
    attr_bbox = draw.textbbox((0, 0), attr_text, font=attr_font)
    attr_h = attr_bbox[3] - attr_bbox[1]
    attr_area_h = attr_h + 20  # rule + padding

    # Quote text zone
    quote_top = header_h + 16
    quote_bottom = height - attr_area_h - margin
    fit_h = int((quote_bottom - quote_top) * 0.95)
    quote_zone_w = width - 2 * margin

    quote_font, quote_lines = _fit_font(draw, quote_text, fp, quote_zone_w, fit_h)

    line_h = draw.textbbox((0, 0), "Ay", font=quote_font)[3] + 6
    total_text_h = line_h * len(quote_lines)
    zone_mid = quote_top + (quote_bottom - quote_top) // 2
    text_y = zone_mid - total_text_h // 2

    for line in quote_lines:
        lw = draw.textbbox((0, 0), line, font=quote_font)[2]
        x = (width - lw) // 2
        draw.text((x, text_y), line, fill="black", font=quote_font)
        text_y += line_h

    # Horizontal rule
    rule_y = height - attr_area_h - 4
    draw.line([(margin, rule_y), (width - margin, rule_y)], fill=(200, 200, 200), width=1)

    # Attribution
    attr_y = rule_y + 10
    attr_x = width - margin - (attr_bbox[2] - attr_bbox[0])
    draw.text((attr_x, attr_y), attr_text, fill=(120, 120, 120), font=attr_font)

    bg.save(output_path)
    print(f"[quote] Saved to {output_path}")
    return output_path


def generate(config):
    """Generate Quote of the Day image. Return output path."""
    quote_cfg = config.get("quote_of_day", {})
    output_path = quote_cfg.get("output_path", "quote_display.bmp")

    data = _load_cache()
    if not data:
        data = _fetch_quote()
        if data:
            _save_cache(data)
        else:
            print("[quote] Using hardcoded fallback quote.")
            data = {"q": FALLBACK_QUOTE, "a": FALLBACK_AUTHOR}

    return _render(data["q"], data["a"], output_path)


if __name__ == "__main__":
    import yaml
    with open("config.yml") as f:
        cfg = yaml.safe_load(f)
    path = generate(cfg)
    print(f"Output: {path}")
