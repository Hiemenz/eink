"""
Wikipedia Picture of the Day module.

Fetches today's featured image from the Wikipedia REST API, downloads it,
resizes/crops to 800x480, overlays a caption band at the bottom, and saves
as a BMP.
"""

import os
import platform
import requests
from datetime import date
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont


HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EinkDisplay/1.0)"}
CACHE_DIR = "data"


def _font_path():
    if platform.system() == "Darwin":
        return "/Library/Fonts/Arial Unicode.ttf"
    return "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"


def _today_cache_path():
    today = date.today().isoformat()
    return os.path.join(CACHE_DIR, f"wiki_image_{today}.bmp")


def _fetch_featured(today=None):
    """
    Fetch today's featured content from Wikipedia REST API.
    Returns (image_url, caption_text) or (None, None).
    """
    if today is None:
        today = date.today()
    y, m, d = today.year, today.month, today.day
    url = f"https://en.wikipedia.org/api/rest_v1/feed/featured/{y}/{m:02d}/{d:02d}"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[wiki] Failed to fetch featured content: {e}")
        return None, None

    # image.image.source is the full-res URL
    image_data = data.get("image", {})
    image_url = (
        image_data.get("image", {}).get("source")
        or image_data.get("thumbnail", {}).get("source")
    )
    caption = (
        image_data.get("description", {}).get("text")
        or image_data.get("title", "Wikipedia Picture of the Day")
    )
    # Strip HTML tags from caption
    import re
    caption = re.sub(r"<[^>]+>", "", caption).strip()

    return image_url, caption


def _download_image(image_url):
    try:
        resp = requests.get(image_url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content)).convert("RGB")
        return img
    except Exception as e:
        print(f"[wiki] Could not download image: {e}")
        return None


def _render(img, caption, output_path, width=800, height=480):
    # Resize image to fill canvas (crop to maintain aspect ratio)
    ratio = max(width / img.width, height / img.height)
    new_w = int(img.width * ratio)
    new_h = int(img.height * ratio)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - width) // 2
    top = (new_h - height) // 2
    img = img.crop((left, top, left + width, top + height))

    draw = ImageDraw.Draw(img)

    # Caption band at the bottom
    band_h = 60
    band_top = height - band_h
    # Semi-transparent dark band via rectangle with alpha blend
    overlay = Image.new("RGBA", (width, band_h), (0, 0, 0, 180))
    img_rgba = img.convert("RGBA")
    img_rgba.paste(overlay, (0, band_top), overlay)
    img = img_rgba.convert("RGB")
    draw = ImageDraw.Draw(img)

    fp = _font_path()
    margin = 8
    text_w = width - 2 * margin

    # Fit caption into band
    for size in range(22, 9, -2):
        try:
            font = ImageFont.truetype(fp, size)
        except Exception:
            font = ImageFont.load_default()
        words = caption.split()
        lines = []
        current = []
        for word in words:
            test = ' '.join(current + [word])
            if draw.textbbox((0, 0), test, font=font)[2] <= text_w:
                current.append(word)
            else:
                if current:
                    lines.append(' '.join(current))
                current = [word]
        if current:
            lines.append(' '.join(current))

        total_h = len(lines) * (draw.textbbox((0, 0), "Ag", font=font)[3] + 2)
        if total_h <= band_h - 2 * margin:
            break

    y = band_top + margin
    for line in lines:
        draw.text((margin, y), line, fill="white", font=font)
        y += draw.textbbox((0, 0), line, font=font)[3] + 2

    img.save(output_path)
    print(f"[wiki] Saved to {output_path}")
    return output_path


def _error_image(output_path, width=800, height=480, message="Wikipedia image unavailable today."):
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
    """Generate Wikipedia Image of the Day. Return output path."""
    wiki_cfg = config.get("wiki_image", {})
    output_path = wiki_cfg.get("output_path", "wiki_display.bmp")
    width = config.get("width", 800)
    height = config.get("height", 480)

    # Use cached BMP if already generated today
    cached = _today_cache_path()
    if os.path.exists(cached) and cached == output_path:
        print(f"[wiki] Using cached image: {cached}")
        return cached

    image_url, caption = _fetch_featured()
    if not image_url:
        return _error_image(output_path, width, height)

    img = _download_image(image_url)
    if not img:
        return _error_image(output_path, width, height)

    return _render(img, caption or "Wikipedia Picture of the Day", output_path, width, height)


if __name__ == "__main__":
    import yaml
    with open("config.yml") as f:
        cfg = yaml.safe_load(f)
    path = generate(cfg)
    print(f"Output: {path}")
