"""
Questions module.

Displays a rotating question or fact from a CSV file. The question changes
every `interval_minutes` minutes. No state file needed — question selection
is deterministic per time bucket so the same question always shows within
a given interval window.

CSV format expected: topic,question  (header row required)
Default CSV: data/questions/eink_facts.csv
"""

import csv
import math
import os
import platform
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
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
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
# Question loading
# ---------------------------------------------------------------------------

def _load_questions(csv_path):
    """Return list of (topic, question) tuples from the CSV."""
    rows = []
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                q = row.get("question", "").strip()
                t = row.get("topic", "").strip()
                if q:
                    rows.append((t, q))
    except Exception as e:
        print(f"[questions] Failed to load CSV {csv_path}: {e}")
    return rows


def _pick_question(questions, interval_minutes):
    """Pick a question deterministically for the current time bucket."""
    bucket = int(time.time()) // (interval_minutes * 60)
    rng = random.Random(bucket)
    idx = rng.randint(0, len(questions) - 1)
    return questions[idx]


def _minutes_until_next(interval_minutes):
    """Return seconds remaining until the next question change."""
    interval_s = interval_minutes * 60
    elapsed = int(time.time()) % interval_s
    return interval_s - elapsed


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

def _render(topic, question, interval_minutes, output_path, width=800, height=480):
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    margin = 32
    content_w = width - 2 * margin

    # Auto-fit font: start at 42px, shrink until text fits
    font_size = 42
    while font_size >= 18:
        font = _load_font(font_size)
        wrapped = _wrap(question, font, draw, content_w)
        bbox = draw.multiline_textbbox((0, 0), wrapped, font=font)
        th = bbox[3] - bbox[1]
        if th <= height - 100:   # leave room for topic + footer
            break
        font_size -= 2

    font = _load_font(font_size)
    wrapped = _wrap(question, font, draw, content_w)
    bbox = draw.multiline_textbbox((0, 0), wrapped, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    # Center text vertically with slight upward offset to leave footer room
    x = margin
    y = max(margin, (height - th) // 2 - 20)

    draw.multiline_text((x, y), wrapped, fill=(20, 20, 20), font=font, align="left")

    # Topic label (top-right)
    if topic:
        tf = _load_font(15)
        tw2 = draw.textbbox((0, 0), topic, font=tf)[2]
        draw.text((width - margin - tw2, 14), topic, fill=(160, 160, 160), font=tf)

    # Footer: next change countdown
    secs = _minutes_until_next(interval_minutes)
    mins_left = math.ceil(secs / 60)
    footer = f"Changes in {mins_left} min  •  every {interval_minutes} min"
    ff = _load_font(13)
    fw = draw.textbbox((0, 0), footer, font=ff)[2]
    draw.text((width - margin - fw, height - 18), footer, fill=(190, 190, 190), font=ff)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path)
    print(f"[questions] Saved to {output_path} (topic={topic!r}, font={font_size}px)")
    return output_path


def _render_fallback(output_path):
    img = Image.new("RGB", (800, 480), "white")
    draw = ImageDraw.Draw(img)
    draw.text((32, 200), "No questions found.", fill=(80, 80, 80), font=_load_font(28))
    draw.text((32, 244), "Check questions.csv_file in config.", fill=(130, 130, 130), font=_load_font(18))
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path)
    return output_path


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def generate(config):
    """Generate question image. Return output path."""
    cfg = config.get("questions", {})
    output_path = cfg.get("output_path", "images/questions_display.bmp")
    interval_minutes = int(cfg.get("interval_minutes", 15))
    csv_path = cfg.get("csv_file", "data/questions/eink_facts.csv")
    width = config.get("width", 800)
    height = config.get("height", 480)

    questions = _load_questions(csv_path)
    if not questions:
        return _render_fallback(output_path)

    topic, question = _pick_question(questions, interval_minutes)
    return _render(topic, question, interval_minutes, output_path, width, height)


if __name__ == "__main__":
    import yaml
    with open("config.yml") as f:
        cfg = yaml.safe_load(f)
    path = generate(cfg)
    print(f"Output: {path}")
