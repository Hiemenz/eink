"""
Interesting Fact module.

Displays a rotating interesting fact from a CSV file. The fact changes
every `interval_minutes` minutes (default 60). No state file needed —
fact selection is deterministic per time bucket so the same fact always
shows within a given interval window.

CSV format expected: topic,question  (header row required)
Default CSV: data/questions/eink_facts.csv
"""

import csv
import os
import random
import time
from PIL import Image, ImageDraw, ImageFont


# ---------------------------------------------------------------------------
# Font helpers
# ---------------------------------------------------------------------------

_FONT_CANDIDATES = [
    "/Library/Fonts/Arial Unicode.ttf",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _load_font(size):
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Fact loading — module-level cache for performance
# ---------------------------------------------------------------------------

_facts_cache: list = []
_facts_cache_path: str = ""


def _load_facts(csv_path):
    """Return cached list of (topic, fact) tuples, loading from CSV if needed."""
    global _facts_cache, _facts_cache_path
    if _facts_cache and _facts_cache_path == csv_path:
        return _facts_cache
    rows = []
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                fact = row.get("question", "").strip()
                topic = row.get("topic", "").strip()
                if fact:
                    rows.append((topic, fact))
    except Exception as e:
        print(f"[interesting_fact] Failed to load CSV {csv_path}: {e}")
    _facts_cache = rows
    _facts_cache_path = csv_path
    return rows


def _pick_fact(facts, interval_minutes):
    """Pick a fact deterministically for the current time bucket."""
    idx = random.Random(int(time.time()) // (interval_minutes * 60)).randint(0, len(facts) - 1)
    return facts[idx]


# ---------------------------------------------------------------------------
# Text wrapping
# ---------------------------------------------------------------------------

def _wrap(text, font, draw, max_width):
    words = text.split()
    if not words:
        return ""
    lines, line = [], words[0]
    for word in words[1:]:
        test = line + " " + word
        if draw.textbbox((0, 0), test, font=font)[2] <= max_width:
            line = test
        else:
            lines.append(line)
            line = word
    lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render(fact, interval_minutes, output_path, width=800, height=480):
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    margin = 20
    content_w = width - 2 * margin

    # --- Header ---
    header_text = "Interesting Fact"
    header_font = _load_font(32)
    header_bbox = draw.textbbox((0, 0), header_text, font=header_font)
    header_h = header_bbox[3] - header_bbox[1]
    header_y = margin
    draw.text((margin, header_y), header_text, fill=(20, 20, 20), font=header_font)

    # Thin horizontal rule below header
    rule_y = header_y + header_h + 8
    draw.line([(margin, rule_y), (width - margin, rule_y)], fill=(180, 180, 180), width=1)

    content_top = rule_y + 12

    # Footer height reservation
    footer_font = _load_font(12)
    footer_text = f"Updates every {interval_minutes} min"
    footer_bbox = draw.textbbox((0, 0), footer_text, font=footer_font)
    footer_h = footer_bbox[3] - footer_bbox[1]
    footer_margin = 10
    content_bottom = height - footer_h - footer_margin * 2

    available_h = content_bottom - content_top

    # Auto-fit font: start at 36px, shrink to 16px
    font_size = 36
    wrapped = ""
    while font_size >= 16:
        font = _load_font(font_size)
        wrapped = _wrap(fact, font, draw, content_w)
        bbox = draw.multiline_textbbox((0, 0), wrapped, font=font)
        text_h = bbox[3] - bbox[1]
        if text_h <= available_h:
            break
        font_size -= 2

    font = _load_font(font_size)
    wrapped = _wrap(fact, font, draw, content_w)
    bbox = draw.multiline_textbbox((0, 0), wrapped, font=font)
    text_h = bbox[3] - bbox[1]

    # Center fact text vertically in available area
    text_y = content_top + max(0, (available_h - text_h) // 2)

    draw.multiline_text((margin, text_y), wrapped, fill=(20, 20, 20), font=font, align="left")

    # Bottom-right footer label
    fw = footer_bbox[2] - footer_bbox[0]
    footer_x = width - margin - fw
    footer_y = height - footer_h - footer_margin
    draw.text((footer_x, footer_y), footer_text, fill=(160, 160, 160), font=footer_font)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path)
    print(f"[interesting_fact] Saved to {output_path} (font={font_size}px)")
    return output_path


def _render_fallback(output_path):
    img = Image.new("RGB", (800, 480), "white")
    draw = ImageDraw.Draw(img)
    draw.text((32, 200), "No facts found.", fill=(80, 80, 80), font=_load_font(28))
    draw.text((32, 244), "Check csv_file in config.", fill=(130, 130, 130), font=_load_font(18))
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path)
    return output_path


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def generate(config):
    """Generate interesting fact image. Return output path."""
    cfg = config.get("interesting_fact", {})
    output_path = cfg.get("output_path", "images/interesting_fact.bmp")
    interval_minutes = int(cfg.get("interval_minutes", 60))
    csv_path = cfg.get("csv_file", "data/questions/eink_facts.csv")
    width = config.get("width", 800)
    height = config.get("height", 480)

    facts = _load_facts(csv_path)
    if not facts:
        return _render_fallback(output_path)

    _topic, fact = _pick_fact(facts, interval_minutes)
    return _render(fact, interval_minutes, output_path, width, height)


if __name__ == "__main__":
    import yaml
    with open("config.yml") as f:
        cfg = yaml.safe_load(f)
    path = generate(cfg)
    print(f"Output: {path}")
