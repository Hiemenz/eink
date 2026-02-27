"""
Poem of the Day module.

Fetches a random poem from PoetryDB and renders it as an elegant
800x480 BMP with a text-only layout on a white background.

Layout:
  - Top: small "Poem of the Day" header, centered, gray
  - Title: large black, centered
  - Author: medium gray "by Author Name", centered
  - Thin horizontal rule
  - Poem lines: left-aligned with a left margin, auto-sized font
  - If poem is too long to fit, lines are truncated with "..."
"""

import os
import json
import platform
import requests
from datetime import date
from PIL import Image, ImageDraw, ImageFont


HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EinkDisplay/1.0)"}
CACHE_DIR = "data"
FALLBACK_POEM = {
    "title": "Hope",
    "author": "Emily Dickinson",
    "lines": [
        '"Hope" is the thing with feathers -',
        "That perches in the soul -",
        "And sings the tune without the words -",
        "And never stops - at all -",
    ],
}


def _font_path():
    if platform.system() == "Darwin":
        return "/Library/Fonts/Arial Unicode.ttf"
    return "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"


def _cache_path():
    today = date.today().isoformat()
    return os.path.join(CACHE_DIR, f"poem_cache_{today}.json")


def _load_cache():
    path = _cache_path()
    if os.path.exists(path):
        print(f"[poem] Loading cached poem from {path}")
        with open(path) as f:
            return json.load(f)
    return None


def _save_cache(data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(), "w") as f:
        json.dump(data, f)


def _fetch_poem():
    try:
        resp = requests.get("https://poetrydb.org/random/1", headers=HEADERS, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
        if payload and isinstance(payload, list):
            entry = payload[0]
            title = entry.get("title", "").strip()
            author = entry.get("author", "").strip()
            lines = entry.get("lines", [])
            if title and author and lines:
                print(f"[poem] Fetched '{title}' by {author} ({len(lines)} lines)")
                return {"title": title, "author": author, "lines": lines}
    except Exception as e:
        print(f"[poem] API request failed: {e}")
    return None


def _fit_lines_font(draw, lines, font_path, max_width, max_height, start_size=22, min_size=10):
    """Find the largest font size where all lines fit within the given box."""
    for size in range(start_size, min_size - 1, -1):
        try:
            font = ImageFont.truetype(font_path, size)
        except Exception:
            font = ImageFont.load_default()
        line_h = draw.textbbox((0, 0), "Ay", font=font)[3] + 4
        total_h = line_h * len(lines)
        max_line_w = max(
            draw.textbbox((0, 0), line, font=font)[2] for line in lines
        ) if lines else 0
        if total_h <= max_height and max_line_w <= max_width:
            return font, size
    try:
        font = ImageFont.truetype(font_path, min_size)
    except Exception:
        font = ImageFont.load_default()
    return font, min_size


def _truncate_lines(draw, lines, font, max_width, max_height):
    """Return only the lines that fit vertically; replace the last with '...' if truncated."""
    line_h = draw.textbbox((0, 0), "Ay", font=font)[3] + 4
    max_lines = max(1, max_height // line_h)
    if len(lines) <= max_lines:
        return lines
    visible = list(lines[: max_lines - 1])
    visible.append("...")
    return visible


def _render(title, author, lines, output_path, width=800, height=480):
    bg = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(bg)
    fp = _font_path()
    margin = 40

    # --- Header ---
    try:
        header_font = ImageFont.truetype(fp, 14)
    except Exception:
        header_font = ImageFont.load_default()
    header_text = "Poem of the Day"
    hw = draw.textbbox((0, 0), header_text, font=header_font)[2]
    draw.text(((width - hw) // 2, 8), header_text, fill=(160, 160, 160), font=header_font)
    y = 8 + draw.textbbox((0, 0), header_text, font=header_font)[3] + 6

    # --- Title ---
    title_size = 32
    while title_size > 14:
        try:
            title_font = ImageFont.truetype(fp, title_size)
        except Exception:
            title_font = ImageFont.load_default()
        tw = draw.textbbox((0, 0), title, font=title_font)[2]
        if tw <= width - 2 * margin:
            break
        title_size -= 2
    else:
        try:
            title_font = ImageFont.truetype(fp, 14)
        except Exception:
            title_font = ImageFont.load_default()
    tw = draw.textbbox((0, 0), title, font=title_font)[2]
    draw.text(((width - tw) // 2, y), title, fill=(0, 0, 0), font=title_font)
    y += draw.textbbox((0, 0), title, font=title_font)[3] + 6

    # --- Author ---
    try:
        author_font = ImageFont.truetype(fp, 18)
    except Exception:
        author_font = ImageFont.load_default()
    author_text = f"by {author}"
    aw = draw.textbbox((0, 0), author_text, font=author_font)[2]
    draw.text(((width - aw) // 2, y), author_text, fill=(130, 130, 130), font=author_font)
    y += draw.textbbox((0, 0), author_text, font=author_font)[3] + 10

    # --- Horizontal rule ---
    draw.line([(margin, y), (width - margin, y)], fill=(200, 200, 200), width=1)
    y += 10

    # --- Poem lines ---
    poem_area_w = width - margin - margin
    poem_area_h = height - y - 10

    stripped = list(lines)
    while stripped and stripped[0].strip() == "":
        stripped = stripped[1:]
    while stripped and stripped[-1].strip() == "":
        stripped = stripped[:-1]

    if not stripped:
        stripped = ["(no lines)"]

    start_size = 18 if len(stripped) <= 12 else 14 if len(stripped) <= 20 else 11
    poem_font, _ = _fit_lines_font(
        draw, stripped, fp, poem_area_w, poem_area_h, start_size=start_size, min_size=9
    )

    visible_lines = _truncate_lines(draw, stripped, poem_font, poem_area_w, poem_area_h)

    line_h = draw.textbbox((0, 0), "Ay", font=poem_font)[3] + 4
    for line in visible_lines:
        draw.text((margin, y), line, fill=(30, 30, 30), font=poem_font)
        y += line_h

    bg.save(output_path)
    print(f"[poem] Saved to {output_path}")
    return output_path


def generate(config):
    """Generate Poem of the Day image. Return output path."""
    cfg = config.get("poem_of_day", {})
    output_path = cfg.get("output_path", "poem_display.bmp")

    data = _load_cache()
    if not data:
        data = _fetch_poem()
        if data:
            _save_cache(data)
        else:
            print("[poem] Using hardcoded fallback poem.")
            data = FALLBACK_POEM

    return _render(data["title"], data["author"], data["lines"], output_path)


if __name__ == "__main__":
    import yaml
    with open("config.yml") as f:
        cfg = yaml.safe_load(f)
    path = generate(cfg)
    print(f"Output: {path}")
