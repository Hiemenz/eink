"""
Saint of the Day module.

Scrapes today's saint from franciscanmedia.org, attempts to fetch a portrait
image, then renders name + feast day + bio on an 800x480 BMP.

Layout:
  - If portrait found: left ~340px = image, right = text
  - No portrait:       full-width centered text
"""

import os
import json
import requests
import platform
from datetime import date
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _font_path():
    if platform.system() == "Darwin":
        return "/Library/Fonts/Arial Unicode.ttf"
    return "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"


def _wrap_text(draw, text, font, max_width):
    """Return a list of lines that fit within max_width."""
    words = text.split()
    lines = []
    current = []
    for word in words:
        test = ' '.join(current + [word])
        w = draw.textbbox((0, 0), test, font=font)[2]
        if w <= max_width:
            current.append(word)
        else:
            if current:
                lines.append(' '.join(current))
            current = [word]
    if current:
        lines.append(' '.join(current))
    return lines


def _fit_font(draw, text, font_path, max_width, max_height, start_size=48, min_size=12):
    """Return (font, wrapped_lines) at the largest size that fits."""
    for size in range(start_size, min_size - 1, -2):
        try:
            font = ImageFont.truetype(font_path, size)
        except Exception:
            font = ImageFont.load_default()
        lines = _wrap_text(draw, text, font, max_width)
        total_h = sum(draw.textbbox((0, 0), ln, font=font)[3] for ln in lines) + 4 * len(lines)
        if total_h <= max_height:
            return font, lines
    try:
        font = ImageFont.truetype(font_path, min_size)
    except Exception:
        font = ImageFont.load_default()
    return font, _wrap_text(draw, text, font, max_width)


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EinkDisplay/1.0)"}
CACHE_DIR = "data"


def _cache_path():
    today = date.today().isoformat()
    return os.path.join(CACHE_DIR, f"saint_cache_{today}.json")


def _load_cache():
    path = _cache_path()
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


def _save_cache(data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(), 'w') as f:
        json.dump(data, f)


def _scrape_franciscan():
    """Return dict with name, feast_day, bio, image_url (may be None)."""
    url = "https://www.franciscanmedia.org/saint-of-the-day/"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"[saint] Failed to fetch franciscanmedia.org: {e}")
        return None

    soup = BeautifulSoup(resp.text, "lxml")

    # Saint name — usually an <h1> or <h2> in the main article
    name = None
    for tag in ("h1", "h2"):
        el = soup.find(tag)
        if el:
            name = el.get_text(strip=True)
            break
    if not name:
        name = "Saint of the Day"

    # Portrait image — first <img> inside the article content
    image_url = None
    article = soup.find("article") or soup.find("main") or soup
    img_tag = article.find("img") if article else None
    if img_tag:
        src = img_tag.get("src") or img_tag.get("data-src", "")
        if src.startswith("http"):
            image_url = src

    # Bio — first substantial <p> under the article
    bio = ""
    if article:
        for p in article.find_all("p"):
            text = p.get_text(strip=True)
            if len(text) > 80:
                bio = text
                break

    # Feast day — look for a line containing "feast" or date pattern
    feast_day = date.today().strftime("%B %d")
    for p in (article or soup).find_all("p"):
        txt = p.get_text(strip=True)
        if "feast" in txt.lower() or any(m in txt for m in ["January","February","March","April","May","June","July","August","September","October","November","December"]):
            feast_day = txt[:80]
            break

    return {"name": name, "feast_day": feast_day, "bio": bio, "image_url": image_url}


def _fetch_portrait(image_url):
    """Download and return a PIL Image, or None on failure."""
    if not image_url:
        return None
    try:
        resp = requests.get(image_url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content)).convert("RGB")
        return img
    except Exception as e:
        print(f"[saint] Could not fetch portrait: {e}")
        return None


# ---------------------------------------------------------------------------
# Image rendering
# ---------------------------------------------------------------------------

def _render(data, portrait, output_path, width=800, height=480):
    bg = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(bg)
    fp = _font_path()
    margin = 16

    if portrait:
        # Left panel: portrait
        panel_w = 340
        text_x = panel_w + margin
        text_w = width - text_x - margin

        # Scale portrait to fill left panel
        ratio = min(panel_w / portrait.width, height / portrait.height)
        pw = int(portrait.width * ratio)
        ph = int(portrait.height * ratio)
        portrait_resized = portrait.resize((pw, ph), Image.LANCZOS)
        py = (height - ph) // 2
        bg.paste(portrait_resized, (0, py))

        # Dividing line
        draw.line([(panel_w, 0), (panel_w, height)], fill="lightgray", width=1)
    else:
        text_x = margin
        text_w = width - 2 * margin

    # Name (large)
    y = margin
    try:
        name_font = ImageFont.truetype(fp, 42)
    except Exception:
        name_font = ImageFont.load_default()
    name_lines = _wrap_text(draw, data["name"], name_font, text_w)
    for line in name_lines:
        draw.text((text_x, y), line, fill="black", font=name_font)
        y += draw.textbbox((0, 0), line, font=name_font)[3] + 4
    y += 6

    # Feast day (italic-ish, smaller)
    try:
        feast_font = ImageFont.truetype(fp, 22)
    except Exception:
        feast_font = ImageFont.load_default()
    feast_lines = _wrap_text(draw, data["feast_day"], feast_font, text_w)
    for line in feast_lines:
        draw.text((text_x, y), line, fill=(80, 80, 80), font=feast_font)
        y += draw.textbbox((0, 0), line, font=feast_font)[3] + 3
    y += 10

    # Horizontal rule
    draw.line([(text_x, y), (width - margin, y)], fill="lightgray", width=1)
    y += 8

    # Bio — fit remaining space
    remaining_h = height - y - margin
    if data["bio"] and remaining_h > 20:
        bio_font, bio_lines = _fit_font(draw, data["bio"], fp, text_w, remaining_h, start_size=22, min_size=10)
        for line in bio_lines:
            lh = draw.textbbox((0, 0), line, font=bio_font)[3] + 3
            if y + lh > height - margin:
                break
            draw.text((text_x, y), line, fill="black", font=bio_font)
            y += lh

    bg.save(output_path)
    print(f"[saint] Saved to {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def generate(config):
    """Generate Saint of the Day image. Return output path."""
    saint_cfg = config.get("saint_of_day", {})
    output_path = saint_cfg.get("output_path", "saint_display.bmp")
    width = config.get("width", 800)
    height = config.get("height", 480)

    data = _load_cache()
    if not data:
        data = _scrape_franciscan()
        if not data:
            # Graceful fallback
            data = {
                "name": "Saint of the Day",
                "feast_day": date.today().strftime("%B %d"),
                "bio": "Could not fetch today's saint. Please check your internet connection.",
                "image_url": None,
            }
        _save_cache(data)

    portrait = _fetch_portrait(data.get("image_url"))
    return _render(data, portrait, output_path, width, height)


if __name__ == "__main__":
    import yaml
    with open("config.yml") as f:
        cfg = yaml.safe_load(f)
    path = generate(cfg)
    print(f"Output: {path}")
