"""
XKCD comic-of-the-day module for an 800x480 Waveshare e-ink display.

Fetches XKCD comic metadata from the keyless public JSON API, downloads the
comic image, and renders a clean 800x480 RGB BMP: a bold centered title with
the comic number/date, the letterboxed comic art in the center, and the
`alt` (hover) caption word-wrapped along the bottom.

Data source (no API key required):
  - Latest comic:   https://xkcd.com/info.0.json
  - Specific comic: https://xkcd.com/{N}/info.0.json
  Each JSON has: num, title, img (PNG URL), alt, year, month, day.

Modes:
  - "latest" (default): show the newest published comic.
  - "daily": deterministically pick a comic based on today's date, so it
    changes each day but stays stable within a day.

The chosen comic's metadata and downloaded image are cached under the cache
directory. On a fetch failure the cached comic is reused; if nothing is
cached either, an "XKCD unavailable" card is rendered.

Config section (add to config.yml):

    xkcd:
      output_path: images/xkcd_display.bmp
      mode: latest        # latest | daily
      cache_dir: data/
"""

import os
import sys
import json
import random
from io import BytesIO
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from PIL import Image, ImageDraw

from utils import get_font, get_logger

logger = get_logger("xkcd")

WIDTH, HEIGHT = 800, 480
MARGIN = 16
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EinkDisplay/1.0; +xkcd)"}

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
GRAY = (110, 110, 110)


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #
def _fetch_json(url):
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _fetch_latest():
    return _fetch_json("https://xkcd.com/info.0.json")


def _fetch_num(n):
    return _fetch_json(f"https://xkcd.com/{n}/info.0.json")


def _choose_comic(mode):
    """Return comic metadata dict for the chosen comic, or None on failure."""
    latest = _fetch_latest()
    latest_num = int(latest.get("num", 0))

    if mode == "daily" and latest_num > 1:
        seed = date.today().isoformat()
        rng = random.Random(seed)
        chosen = rng.randint(1, latest_num)
        # Comic #404 famously returns a 404 (the joke); pick a neighbor.
        if chosen == 404:
            chosen = 403
        if chosen == latest_num:
            return latest
        try:
            return _fetch_num(chosen)
        except Exception as e:
            logger.warning("Daily comic #%s fetch failed (%s); using latest", chosen, e)
            return latest

    return latest


def _download_image(url):
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return Image.open(BytesIO(resp.content)).convert("RGB")


# --------------------------------------------------------------------------- #
# Caching
# --------------------------------------------------------------------------- #
def _cache_paths(cache_dir):
    return (
        os.path.join(cache_dir, "xkcd_cache.json"),
        os.path.join(cache_dir, "xkcd_current.png"),
    )


def _save_cache(cache_dir, meta, img):
    os.makedirs(cache_dir, exist_ok=True)
    meta_path, img_path = _cache_paths(cache_dir)
    try:
        img.save(img_path, "PNG")
        with open(meta_path, "w") as fh:
            json.dump(meta, fh)
    except Exception as e:
        logger.warning("Failed to write cache: %s", e)


def _load_cache(cache_dir):
    """Return (meta, PIL.Image) from cache, or (None, None) if unavailable."""
    meta_path, img_path = _cache_paths(cache_dir)
    try:
        with open(meta_path) as fh:
            meta = json.load(fh)
        img = Image.open(img_path).convert("RGB")
        return meta, img
    except Exception:
        return None, None


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #
def _text_w(draw, text, font):
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def _fit_font(draw, text, max_w, start_size, min_size, bold=False):
    """Largest font (start..min) whose single-line render fits max_w."""
    size = start_size
    while size > min_size:
        font = get_font(size, bold=bold)
        if _text_w(draw, text, font) <= max_w:
            return font
        size -= 1
    return get_font(min_size, bold=bold)


def _wrap(draw, text, font, max_w):
    words = text.split()
    lines, current = [], []
    for word in words:
        test = " ".join(current + [word])
        if _text_w(draw, test, font) <= max_w or not current:
            current.append(word)
        else:
            lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


def _wrap_clamped(draw, text, font, max_w, max_lines):
    """Word-wrap, clamping to max_lines with a trailing ellipsis if truncated."""
    lines = _wrap(draw, text, font, max_w)
    if len(lines) <= max_lines:
        return lines
    lines = lines[:max_lines]
    last = lines[-1]
    while last and _text_w(draw, last + "…", font) > max_w:
        last = last[:-1].rstrip()
    lines[-1] = last + "…"
    return lines


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _error_image(output_path, message="XKCD unavailable"):
    img = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(img)
    font = get_font(40, bold=True)
    w = _text_w(draw, message, font)
    box = draw.textbbox((0, 0), message, font=font)
    h = box[3] - box[1]
    draw.text(((WIDTH - w) // 2, (HEIGHT - h) // 2), message, fill=BLACK, font=font)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path)
    return output_path


def _render(meta, comic_img, output_path):
    img = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(img)

    title = (meta.get("title") or "XKCD").strip()
    num = meta.get("num", "")
    try:
        date_str = date(int(meta["year"]), int(meta["month"]), int(meta["day"])).strftime("%b %-d, %Y")
    except Exception:
        date_str = ""
    subtitle = "  ".join(p for p in [f"#{num}" if num != "" else "", date_str] if p)

    content_w = WIDTH - 2 * MARGIN

    # --- Title (bold, centered, auto-sized ~24px) ---
    title_font = _fit_font(draw, title, content_w, 26, 14, bold=True)
    tb = draw.textbbox((0, 0), title, font=title_font)
    title_h = tb[3] - tb[1]
    y = MARGIN
    draw.text(((WIDTH - (tb[2] - tb[0])) // 2, y - tb[1]), title, fill=BLACK, font=title_font)
    y += title_h + 4

    # --- Subtitle (#num + date), smaller gray, centered ---
    if subtitle:
        sub_font = get_font(15)
        sb = draw.textbbox((0, 0), subtitle, font=sub_font)
        draw.text(((WIDTH - (sb[2] - sb[0])) // 2, y - sb[1]), subtitle, fill=GRAY, font=sub_font)
        y += (sb[3] - sb[1]) + 6

    header_bottom = y

    # --- Caption (alt text), bottom, small gray, up to 3 lines ---
    alt = (meta.get("alt") or "").strip()
    cap_font = get_font(15)
    cap_line_h = (draw.textbbox((0, 0), "Ag", font=cap_font)[3]
                  - draw.textbbox((0, 0), "Ag", font=cap_font)[1]) + 3

    cap_lines = _wrap_clamped(draw, alt, cap_font, content_w, 3) if alt else []
    caption_block_h = len(cap_lines) * cap_line_h
    caption_top = HEIGHT - MARGIN - caption_block_h if cap_lines else HEIGHT - MARGIN

    # --- Comic image: fit within the space between header and caption ---
    avail_top = header_bottom + 6
    avail_bottom = caption_top - 8
    avail_h = max(40, avail_bottom - avail_top)
    avail_w = content_w

    cw, ch = comic_img.size
    scale = min(avail_w / cw, avail_h / ch)
    new_w = max(1, int(cw * scale))
    new_h = max(1, int(ch * scale))
    resized = comic_img.resize((new_w, new_h), Image.LANCZOS)
    ix = (WIDTH - new_w) // 2
    iy = avail_top + (avail_h - new_h) // 2
    img.paste(resized, (ix, iy))

    # --- Draw caption ---
    cy = caption_top
    for line in cap_lines:
        lb = draw.textbbox((0, 0), line, font=cap_font)
        draw.text(((WIDTH - (lb[2] - lb[0])) // 2, cy - lb[1]), line, fill=GRAY, font=cap_font)
        cy += cap_line_h

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path)
    logger.info("Saved XKCD #%s '%s' to %s", num, title, output_path)
    return output_path


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def generate(config):
    """
    Generate the XKCD comic image. Returns the output BMP file path.

    Config section (xkcd in config.yml):
      xkcd:
        output_path: images/xkcd_display.bmp
        mode: latest        # latest | daily
        cache_dir: data/
    """
    cfg = config.get("xkcd", {})
    output_path = cfg.get("output_path", "images/xkcd_display.bmp")
    mode = (cfg.get("mode", "latest") or "latest").strip().lower()
    cache_dir = cfg.get("cache_dir", "data/")

    meta, comic_img = None, None
    try:
        meta = _choose_comic(mode)
        comic_img = _download_image(meta["img"])
        _save_cache(cache_dir, meta, comic_img)
    except Exception as e:
        logger.warning("Fetch failed (%s); falling back to cache", e)
        meta, comic_img = _load_cache(cache_dir)

    if meta is None or comic_img is None:
        logger.error("No comic available (fetch failed and no cache)")
        return _error_image(output_path)

    return _render(meta, comic_img, output_path)


if __name__ == "__main__":
    import yaml

    try:
        with open("config.yml") as fh:
            cfg = yaml.safe_load(fh) or {}
    except Exception:
        cfg = {}
    path = generate(cfg)
    print(f"Output: {path}")
