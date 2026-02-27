"""
News Headlines module.

Fetches top headlines from BBC News RSS feed (fallback: NPR) and renders
them as an 800x480 BMP with numbered bullets on a white background.

Layout:
  - Header: "News" (large, left-aligned) + source name (gray, right-aligned)
  - Timestamp line
  - Horizontal rule
  - 5-6 numbered headlines, each wrapped to 2 lines max
  - Gray separator line between headlines
  - Bottom: small gray cache timestamp
"""

import os
import re
import json
import platform
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from PIL import Image, ImageDraw, ImageFont


HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EinkDisplay/1.0)"}
CACHE_DIR = "data"
CACHE_MAX_MINUTES = 60

BBC_RSS = "http://feeds.bbci.co.uk/news/rss.xml"
NPR_RSS = "https://feeds.npr.org/1001/rss.xml"

FALLBACK_HEADLINES = [
    {"title": "Could not fetch news headlines.", "pub_date": ""},
]


def _font_path():
    if platform.system() == "Darwin":
        return "/Library/Fonts/Arial Unicode.ttf"
    return "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"


def _cache_path():
    return os.path.join(CACHE_DIR, "news_headlines_cache.json")


def _load_cache():
    path = _cache_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        cached_at = datetime.fromisoformat(data["cached_at"])
        now = datetime.now(timezone.utc)
        if cached_at.tzinfo is None:
            cached_at = cached_at.replace(tzinfo=timezone.utc)
        age_minutes = (now - cached_at).total_seconds() / 60
        if age_minutes < CACHE_MAX_MINUTES:
            print(f"[news] Loading cache from {path} (age {age_minutes:.1f} min)")
            return data
        else:
            print(f"[news] Cache expired ({age_minutes:.1f} min old)")
    except Exception as e:
        print(f"[news] Cache read error: {e}")
    return None


def _save_cache(headlines, source):
    os.makedirs(CACHE_DIR, exist_ok=True)
    data = {
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "headlines": headlines,
    }
    with open(_cache_path(), "w") as f:
        json.dump(data, f, indent=2)
    print(f"[news] Cache saved to {_cache_path()}")


def _strip_html(text):
    """Remove HTML tags and decode common entities."""
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&quot;", '"')
    text = text.replace("&#39;", "'")
    text = text.replace("&apos;", "'")
    text = text.replace("\u2019", "'")
    text = text.replace("\u2018", "'")
    text = text.replace("\u201c", '"')
    text = text.replace("\u201d", '"')
    return text.strip()


def _parse_rss(xml_text):
    """Parse RSS XML and return list of headline dicts."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"[news] XML parse error: {e}")
        return None

    headlines = []
    for item in root.iter("item"):
        title_el = item.find("title")
        pub_el = item.find("pubDate")
        if title_el is None or not title_el.text:
            continue
        title = _strip_html(title_el.text)
        pub_date = pub_el.text.strip() if pub_el is not None and pub_el.text else ""
        # Skip items that look like feed-level titles (no pub date, very short)
        if not pub_date and len(title) < 20:
            continue
        headlines.append({"title": title, "pub_date": pub_date})
    return headlines


def _fetch_headlines():
    """Fetch from BBC, fall back to NPR. Returns (headlines, source_name) or (None, None)."""
    sources = [
        (BBC_RSS, "BBC News"),
        (NPR_RSS, "NPR News"),
    ]
    for url, name in sources:
        print(f"[news] Fetching {url}")
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            headlines = _parse_rss(resp.text)
            if headlines:
                print(f"[news] Got {len(headlines)} headlines from {name}")
                return headlines, name
            else:
                print(f"[news] No headlines parsed from {name}")
        except Exception as e:
            print(f"[news] Failed to fetch {name}: {e}")
    return None, None


def _wrap_text(draw, text, font, max_width):
    """Word-wrap text to fit within max_width. Returns list of lines."""
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


def _render(headlines, source, cached_at, output_path, width=800, height=480):
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    fp = _font_path()
    margin = 20
    text_w = width - 2 * margin

    # --- Header row ---
    try:
        header_font = ImageFont.truetype(fp, 42)
    except Exception:
        header_font = ImageFont.load_default()
    try:
        source_font = ImageFont.truetype(fp, 20)
    except Exception:
        source_font = ImageFont.load_default()

    header_text = "News"
    header_bbox = draw.textbbox((0, 0), header_text, font=header_font)
    header_h = header_bbox[3]

    draw.text((margin, margin), header_text, fill="black", font=header_font)

    # Source name right-aligned, vertically centered with header
    source_bbox = draw.textbbox((0, 0), source, font=source_font)
    source_x = width - margin - source_bbox[2]
    source_y = margin + (header_h - source_bbox[3]) // 2
    draw.text((source_x, source_y), source, fill=(130, 130, 130), font=source_font)

    y = margin + header_h + 4

    # Timestamp below header
    try:
        ts_font = ImageFont.truetype(fp, 15)
    except Exception:
        ts_font = ImageFont.load_default()
    try:
        ts_dt = datetime.fromisoformat(cached_at)
        if ts_dt.tzinfo is not None:
            ts_label = ts_dt.strftime("Updated %b %-d, %Y at %-I:%M %p UTC")
        else:
            ts_label = ts_dt.strftime("Updated %b %-d, %Y at %-I:%M %p")
    except Exception:
        ts_label = f"Updated {cached_at}"
    draw.text((margin, y), ts_label, fill=(160, 160, 160), font=ts_font)
    ts_bbox = draw.textbbox((0, 0), ts_label, font=ts_font)
    y += ts_bbox[3] + 8

    # Horizontal rule
    draw.line([(margin, y), (width - margin, y)], fill=(170, 170, 170), width=1)
    y += 10

    # --- Auto-size font for headlines ---
    display = headlines[:6]
    num_items = len(display)
    bottom_reserve = 22
    available_h = height - y - margin - bottom_reserve

    chosen_font = None
    for size in range(22, 10, -1):
        try:
            font = ImageFont.truetype(fp, size)
        except Exception:
            font = ImageFont.load_default()
        line_h = draw.textbbox((0, 0), "Ag", font=font)[3] + 2
        total = 0
        for i, item in enumerate(display):
            label = f"{i + 1}. {item['title']}"
            wrapped = _wrap_text(draw, label, font, text_w)
            lines_used = min(len(wrapped), 2)
            total += lines_used * line_h + 4
            if i < num_items - 1:
                total += 5  # separator line height
        if total <= available_h:
            chosen_font = font
            break

    if chosen_font is None:
        try:
            chosen_font = ImageFont.truetype(fp, 11)
        except Exception:
            chosen_font = ImageFont.load_default()

    line_h = draw.textbbox((0, 0), "Ag", font=chosen_font)[3] + 2

    # --- Render each headline ---
    for i, item in enumerate(display):
        if y >= height - margin - bottom_reserve:
            break
        label = f"{i + 1}. {item['title']}"
        wrapped = _wrap_text(draw, label, chosen_font, text_w)
        # Cap at 2 lines; truncate with ellipsis if needed
        if len(wrapped) > 2:
            wrapped = wrapped[:2]
            last = wrapped[1]
            if len(last) > 3:
                wrapped[1] = last[:-3].rstrip() + "..."

        for line in wrapped:
            if y + line_h > height - margin - bottom_reserve:
                break
            draw.text((margin, y), line, fill="black", font=chosen_font)
            y += line_h

        y += 4  # padding below item

        # Gray separator between items (not after the last one)
        if i < num_items - 1:
            sep_y = y + 1
            draw.line(
                [(margin + 20, sep_y), (width - margin - 20, sep_y)],
                fill=(210, 210, 210),
                width=1,
            )
            y += 6

    # --- Bottom cache timestamp ---
    try:
        bot_font = ImageFont.truetype(fp, 12)
    except Exception:
        bot_font = ImageFont.load_default()
    bot_text = f"Cached: {cached_at[:16].replace('T', ' ')} UTC"
    bot_bbox = draw.textbbox((0, 0), bot_text, font=bot_font)
    bot_x = width - margin - bot_bbox[2]
    bot_y = height - margin - bot_bbox[3] + 4
    draw.text((bot_x, bot_y), bot_text, fill=(190, 190, 190), font=bot_font)

    img.save(output_path)
    print(f"[news] Saved to {output_path}")
    return output_path


def generate(config):
    """Generate News Headlines image. Return output path."""
    cfg = config.get("news_headlines", {})
    output_path = cfg.get("output_path", "news_display.bmp")

    cached = _load_cache()
    if cached:
        headlines = cached["headlines"]
        source = cached.get("source", "News")
        cached_at = cached["cached_at"]
    else:
        headlines, source = _fetch_headlines()
        if headlines:
            _save_cache(headlines, source)
            reloaded = _load_cache()
            if reloaded:
                cached_at = reloaded["cached_at"]
            else:
                cached_at = datetime.now(timezone.utc).isoformat()
        else:
            print("[news] Using fallback headlines.")
            headlines = FALLBACK_HEADLINES
            source = "Offline"
            cached_at = datetime.now(timezone.utc).isoformat()

    return _render(headlines, source, cached_at, output_path)


if __name__ == "__main__":
    import yaml
    with open("config.yml") as f:
        cfg = yaml.safe_load(f)
    path = generate(cfg)
    print(f"Output: {path}")
