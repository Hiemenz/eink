"""
Metropolitan Museum of Art – Artwork of the Day module.

Fetches a deterministic highlighted artwork from the Met's free open API,
downloads the primary image, resizes/crops to 800x480, overlays a caption
band at the bottom, and saves as a BMP.

Cache files:
  data/art_ids_cache.json      — cached object-ID list (rarely changes)
  data/art_YYYY-MM-DD.bmp      — rendered BMP for that date
"""

import json
import os
import platform
import shutil
import requests
from datetime import date
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont


HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EinkDisplay/1.0)"}
CACHE_DIR = "data"

SEARCH_URL = (
    "https://collectionapi.metmuseum.org/public/collection/v1/search"
    "?isHighlight=true&hasImages=true&q=painting"
)
OBJECT_URL = "https://collectionapi.metmuseum.org/public/collection/v1/objects/{}"


def _font_path():
    if platform.system() == "Darwin":
        return "/Library/Fonts/Arial Unicode.ttf"
    return "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"


def _ids_cache_path():
    return os.path.join(CACHE_DIR, "art_ids_cache.json")


def _today_bmp_path():
    today = date.today().isoformat()
    return os.path.join(CACHE_DIR, f"art_{today}.bmp")


def _load_object_ids():
    """Return the highlighted object-ID list, using a local cache when available."""
    ids_path = _ids_cache_path()
    if os.path.exists(ids_path):
        try:
            with open(ids_path) as f:
                data = json.load(f)
            ids = data.get("objectIDs", [])
            if ids:
                print(f"[art] Loaded {len(ids)} object IDs from cache.")
                return ids
        except Exception as e:
            print(f"[art] Could not read IDs cache, re-fetching: {e}")

    print("[art] Fetching highlighted object IDs from Met API ...")
    try:
        resp = requests.get(SEARCH_URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[art] Failed to fetch object ID list: {e}")
        return []

    ids = data.get("objectIDs") or []
    print(f"[art] Retrieved {len(ids)} object IDs from API.")

    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        with open(ids_path, "w") as f:
            json.dump({"objectIDs": ids}, f)
        print(f"[art] Cached object IDs to {ids_path}")
    except Exception as e:
        print(f"[art] Could not write IDs cache: {e}")

    return ids


def _fetch_object(object_id):
    """Fetch a single object record from the Met API. Returns the JSON dict or None."""
    url = OBJECT_URL.format(object_id)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[art] Failed to fetch object {object_id}: {e}")
        return None


def _pick_artwork(object_ids):
    """
    Use today's day-of-year to pick a deterministic artwork that has a
    primaryImage. Tries up to 10 consecutive candidates if the first has no image.
    Returns (object_data dict, image_url str) or (None, None).
    """
    today_yday = date.today().timetuple().tm_yday
    n = len(object_ids)
    for offset in range(10):
        idx = (today_yday + offset) % n
        obj_id = object_ids[idx]
        print(f"[art] Trying object ID {obj_id} (index {idx}) ...")
        data = _fetch_object(obj_id)
        if data is None:
            continue
        image_url = data.get("primaryImage", "").strip()
        if image_url:
            print(f"[art] Selected: {data.get('title', 'Untitled')} by {data.get('artistDisplayName', 'Unknown')}")
            return data, image_url
        print(f"[art] Object {obj_id} has no primaryImage, skipping.")

    print("[art] Could not find an artwork with a primaryImage after 10 attempts.")
    return None, None


def _download_image(image_url):
    try:
        resp = requests.get(image_url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content)).convert("RGB")
        return img
    except Exception as e:
        print(f"[art] Could not download image: {e}")
        return None


def _render(img, title, artist, year, output_path, width=800, height=480):
    """Resize/crop img to width×height, overlay caption band, save BMP."""
    # Scale to cover, then center-crop
    ratio = max(width / img.width, height / img.height)
    new_w = int(img.width * ratio)
    new_h = int(img.height * ratio)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - width) // 2
    top = (new_h - height) // 2
    img = img.crop((left, top, left + width, top + height))

    # Semi-transparent dark band at the bottom (~70px)
    band_h = 70
    band_top = height - band_h
    overlay = Image.new("RGBA", (width, band_h), (0, 0, 0, 180))
    img_rgba = img.convert("RGBA")
    img_rgba.paste(overlay, (0, band_top), overlay)
    img = img_rgba.convert("RGB")
    draw = ImageDraw.Draw(img)

    fp = _font_path()
    margin = 8

    # Line 1: title (~20pt), white
    try:
        title_font = ImageFont.truetype(fp, 20)
    except Exception:
        title_font = ImageFont.load_default()

    max_title_w = width - 2 * margin
    title_text = title or "Untitled"
    while title_text and draw.textbbox((0, 0), title_text, font=title_font)[2] > max_title_w:
        title_text = title_text[:-2] + "\u2026"

    # Line 2: "Artist · Year" (~16pt), light gray
    try:
        subtitle_font = ImageFont.truetype(fp, 16)
    except Exception:
        subtitle_font = ImageFont.load_default()

    parts = [p for p in [artist, year] if p and p.strip()]
    subtitle_text = " \u00b7 ".join(parts) if parts else ""

    title_h = draw.textbbox((0, 0), "Ag", font=title_font)[3]
    subtitle_h = draw.textbbox((0, 0), "Ag", font=subtitle_font)[3]
    total_text_h = title_h + 4 + subtitle_h
    y_start = band_top + (band_h - total_text_h) // 2

    draw.text((margin, y_start), title_text, fill="white", font=title_font)
    if subtitle_text:
        draw.text((margin, y_start + title_h + 4), subtitle_text, fill=(200, 200, 200), font=subtitle_font)

    img.save(output_path)
    print(f"[art] Saved to {output_path}")
    return output_path


def _error_image(output_path, width=800, height=480, message="Met artwork unavailable today."):
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(_font_path(), 28)
    except Exception:
        font = ImageFont.load_default()
    draw.text((40, height // 2 - 20), message, fill="black", font=font)
    img.save(output_path)
    return output_path


def generate(config):
    """Generate the Met Artwork of the Day BMP. Returns the output path."""
    art_cfg = config.get("art_of_day", {})
    output_path = art_cfg.get("output_path", "art_display.bmp")
    width = config.get("width", 800)
    height = config.get("height", 480)

    # Return cached BMP if already generated today
    cached = _today_bmp_path()
    if os.path.exists(cached):
        print(f"[art] Using cached image: {cached}")
        if cached != output_path:
            shutil.copy2(cached, output_path)
        return output_path

    object_ids = _load_object_ids()
    if not object_ids:
        return _error_image(output_path, width, height)

    obj_data, image_url = _pick_artwork(object_ids)
    if not image_url:
        return _error_image(output_path, width, height)

    img = _download_image(image_url)
    if not img:
        return _error_image(output_path, width, height)

    title = obj_data.get("title", "Untitled")
    artist = obj_data.get("artistDisplayName", "")
    year = obj_data.get("objectDate", "")

    _render(img, title, artist, year, cached, width, height)
    if cached != output_path:
        shutil.copy2(cached, output_path)
    return output_path


if __name__ == "__main__":
    import yaml
    with open("config.yml") as f:
        cfg = yaml.safe_load(f)
    path = generate(cfg)
    print(f"Output: {path}")
