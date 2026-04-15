"""
Franklin Five Points Live Camera module.

Fetches a snapshot from the downtown Franklin, TN traffic camera at
Five Points, resizes/crops to 800x480, overlays a location label and
timestamp, and saves as a BMP.
"""

import os
import platform
import requests
from datetime import datetime
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont


HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EinkDisplay/1.0)"}


def _font_path():
    if platform.system() == "Darwin":
        return "/Library/Fonts/Arial Unicode.ttf"
    return "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"


def _fetch_snapshot(camera_alias, stream_host, stream_id):
    """Fetch a JPEG snapshot from ipcamlive. Tries alias URL first, then stream URL."""
    urls = []
    if camera_alias:
        urls.append(f"https://ipcamlive.com/player/snapshot.php?alias={camera_alias}")
    if stream_host and stream_id:
        urls.append(f"https://{stream_host}/streams/{stream_id}/snapshot.jpg")
        urls.append(f"http://{stream_host}/streams/{stream_id}/snapshot.jpg")

    for url in urls:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            ct = resp.headers.get("Content-Type", "")
            if "image" not in ct:
                print(f"[franklin_cam] {url} returned non-image: {ct}")
                continue
            return Image.open(BytesIO(resp.content)).convert("RGB")
        except Exception as e:
            print(f"[franklin_cam] Failed ({url}): {e}")

    return None


def _render(img, label, output_path, width=800, height=480):
    """Resize/crop image to canvas, overlay label and timestamp at the bottom."""
    # Scale to cover canvas, then center-crop
    ratio = max(width / img.width, height / img.height)
    new_w = int(img.width * ratio)
    new_h = int(img.height * ratio)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - width) // 2
    top = (new_h - height) // 2
    img = img.crop((left, top, left + width, top + height))

    # Semi-transparent dark band at the bottom
    band_h = 50
    band_top = height - band_h
    overlay = Image.new("RGBA", (width, band_h), (0, 0, 0, 180))
    img_rgba = img.convert("RGBA")
    img_rgba.paste(overlay, (0, band_top), overlay)
    img = img_rgba.convert("RGB")
    draw = ImageDraw.Draw(img)

    fp = _font_path()
    margin = 10

    # Label text (left-aligned)
    try:
        label_font = ImageFont.truetype(fp, 20)
    except Exception:
        label_font = ImageFont.load_default()
    draw.text((margin, band_top + margin), label, fill="white", font=label_font)

    # Timestamp (right-aligned)
    timestamp = datetime.now().strftime("%b %d, %Y  %I:%M %p")
    try:
        time_font = ImageFont.truetype(fp, 16)
    except Exception:
        time_font = ImageFont.load_default()
    ts_bbox = draw.textbbox((0, 0), timestamp, font=time_font)
    ts_w = ts_bbox[2] - ts_bbox[0]
    draw.text((width - margin - ts_w, band_top + margin + 24), timestamp,
              fill="#cccccc", font=time_font)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path)
    print(f"[franklin_cam] Saved image to {output_path}")
    return output_path


def _error_image(output_path, width=800, height=480, message="Camera unavailable."):
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(_font_path(), 28)
    except Exception:
        font = ImageFont.load_default()
    draw.text((40, height // 2 - 20), message, fill="black", font=font)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path)
    return output_path


def generate(config):
    """Generate Five Points camera snapshot. Return output path."""
    cam_cfg = config.get("franklin_cam", {})
    output_path = cam_cfg.get("output_path", "images/franklin_cam.bmp")
    camera_alias = cam_cfg.get("camera_alias", "603e9a4490992")
    stream_id = cam_cfg.get("stream_id", "604atz1hdklyiqukb")
    stream_host = cam_cfg.get("stream_host", "s96.ipcamlive.com")
    label = cam_cfg.get("label", "Five Points \u2014 Downtown Franklin")
    width = config.get("width", 800)
    height = config.get("height", 480)

    print(f"[franklin_cam] Fetching snapshot (alias={camera_alias})...")
    img = _fetch_snapshot(camera_alias, stream_host, stream_id)
    if img is None:
        return _error_image(output_path, width, height)

    return _render(img, label, output_path, width, height)


if __name__ == "__main__":
    import yaml
    with open("config.yml") as f:
        cfg = yaml.safe_load(f)
    path = generate(cfg)
    print(f"Output: {path}")
