"""
NASA Astronomy Picture of the Day module.

Fetches today's APOD from the NASA API, downloads the image (preferring
hdurl when available), resizes/crops to 800x480, overlays a title band at
the bottom, and saves as a BMP.

If the day's entry is a video rather than an image, a text-only fallback
card is rendered instead.
"""

import os
import platform
import shutil
import requests
from datetime import date
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont


HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EinkDisplay/1.0)"}
CACHE_DIR = "data"
APOD_API = "https://api.nasa.gov/planetary/apod"


def _font_path():
    if platform.system() == "Darwin":
        return "/Library/Fonts/Arial Unicode.ttf"
    return "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"


def _today_cache_path():
    today = date.today().isoformat()
    return os.path.join(CACHE_DIR, f"nasa_apod_{today}.bmp")


def _fetch_apod(api_key="DEMO_KEY", today=None):
    """Fetch today's APOD metadata from NASA API. Returns parsed JSON or None."""
    if today is None:
        today = date.today().isoformat()
    params = {"api_key": api_key, "date": today}
    try:
        resp = requests.get(APOD_API, params=params, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[nasa] Failed to fetch APOD metadata: {e}")
        return None


def _download_image(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content)).convert("RGB")
        return img
    except Exception as e:
        print(f"[nasa] Could not download image: {e}")
        return None


def _render_image(img, title, output_path, width=800, height=480):
    """Resize/crop image to canvas and overlay title band at the bottom."""
    # Scale to cover the canvas, then center-crop
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
    text_w = width - 2 * margin

    # Fit title text into the band
    for size in range(26, 9, -2):
        try:
            font = ImageFont.truetype(fp, size)
        except Exception:
            font = ImageFont.load_default()
        words = title.split()
        lines = []
        current = []
        for word in words:
            test = " ".join(current + [word])
            if draw.textbbox((0, 0), test, font=font)[2] <= text_w:
                current.append(word)
            else:
                if current:
                    lines.append(" ".join(current))
                current = [word]
        if current:
            lines.append(" ".join(current))

        line_h = draw.textbbox((0, 0), "Ag", font=font)[3] + 2
        total_h = len(lines) * line_h
        if total_h <= band_h - 2 * margin:
            break

    y = band_top + margin
    for line in lines:
        draw.text((margin, y), line, fill="white", font=font)
        y += draw.textbbox((0, 0), line, font=font)[3] + 2

    img.save(output_path)
    print(f"[nasa] Saved image to {output_path}")
    return output_path


def _render_text_fallback(title, explanation, output_path, width=800, height=480):
    """Render a plain text card when the APOD entry is a video."""
    img = Image.new("RGB", (width, height), "black")
    draw = ImageDraw.Draw(img)
    fp = _font_path()
    margin = 30
    text_w = width - 2 * margin
    y = margin

    try:
        title_font = ImageFont.truetype(fp, 32)
    except Exception:
        title_font = ImageFont.load_default()

    words = title.split()
    lines = []
    current = []
    for word in words:
        test = " ".join(current + [word])
        if draw.textbbox((0, 0), test, font=title_font)[2] <= text_w:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    for line in lines:
        draw.text((margin, y), line, fill="white", font=title_font)
        y += draw.textbbox((0, 0), line, font=title_font)[3] + 4

    y += 16

    snippet = explanation[:200].strip()
    if len(explanation) > 200:
        snippet += "..."

    try:
        body_font = ImageFont.truetype(fp, 22)
    except Exception:
        body_font = ImageFont.load_default()

    words = snippet.split()
    lines = []
    current = []
    for word in words:
        test = " ".join(current + [word])
        if draw.textbbox((0, 0), test, font=body_font)[2] <= text_w:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    for line in lines:
        draw.text((margin, y), line, fill="#cccccc", font=body_font)
        y += draw.textbbox((0, 0), line, font=body_font)[3] + 3

    try:
        label_font = ImageFont.truetype(fp, 18)
    except Exception:
        label_font = ImageFont.load_default()
    draw.text((margin, height - margin - 20), "NASA Astronomy Picture of the Day  (video)",
              fill="#888888", font=label_font)

    img.save(output_path)
    print(f"[nasa] Saved video-fallback card to {output_path}")
    return output_path


def _error_image(output_path, width=800, height=480, message="NASA APOD unavailable today."):
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
    """Generate NASA Astronomy Picture of the Day. Return output path."""
    nasa_cfg = config.get("nasa_apod", {})
    output_path = nasa_cfg.get("output_path", "nasa_apod.bmp")
    api_key = nasa_cfg.get("api_key", "DEMO_KEY")
    width = config.get("width", 800)
    height = config.get("height", 480)

    os.makedirs(CACHE_DIR, exist_ok=True)

    cached = _today_cache_path()
    if os.path.exists(cached):
        print(f"[nasa] Using cached image: {cached}")
        if cached != output_path:
            shutil.copy2(cached, output_path)
        return output_path

    today = date.today().isoformat()
    print(f"[nasa] Fetching APOD for {today} ...")
    apod = _fetch_apod(api_key=api_key, today=today)
    if apod is None:
        return _error_image(output_path, width, height)

    media_type = apod.get("media_type", "image")
    title = apod.get("title", "NASA Astronomy Picture of the Day")
    explanation = apod.get("explanation", "")

    if media_type == "video":
        print("[nasa] Today's APOD is a video — rendering text fallback.")
        _render_text_fallback(title, explanation, cached, width, height)
        if cached != output_path:
            shutil.copy2(cached, output_path)
        return output_path

    image_url = apod.get("hdurl") or apod.get("url")
    if not image_url:
        print("[nasa] No image URL found in APOD response.")
        return _error_image(output_path, width, height)

    print(f"[nasa] Downloading image: {image_url}")
    img = _download_image(image_url)
    if img is None:
        return _error_image(output_path, width, height)

    _render_image(img, title, cached, width, height)
    if cached != output_path:
        shutil.copy2(cached, output_path)
    return output_path


if __name__ == "__main__":
    import yaml
    with open("config.yml") as f:
        cfg = yaml.safe_load(f)
    path = generate(cfg)
    print(f"Output: {path}")
