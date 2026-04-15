"""
Questions module.

Displays a question or fact from a CSV file. A new question is picked at
random when the display is first shown, then held for `interval_minutes`
minutes. State is persisted so the same question stays on screen across
refreshes within the interval.

Config keys (under questions:):
  output_path:      images/questions_display.bmp
  state_file:       data/questions_state.json
  interval_minutes: 15             # how long to show each question
  csv_file:         data/questions/eink_facts.csv
  force_new:        false          # set true (via !set questions.force_new true)
                                   # to immediately pick a new question

Discord controls:
  !display questions                  — switch to this module
  !set questions.interval_minutes 30  — change the rotation interval
  !set questions.force_new true       — force a new question now
  !set questions.csv_file data/questions/other.csv  — switch question bank

CSV format: topic,question  (header row required)
"""

import csv
import json
import os
import random
import time
from PIL import Image, ImageDraw, ImageFont


STATE_FILE = "data/questions_state.json"

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


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def _load_state(state_file):
    if os.path.exists(state_file):
        try:
            with open(state_file) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_state(state_file, state):
    os.makedirs(os.path.dirname(state_file) or ".", exist_ok=True)
    with open(state_file, "w") as f:
        json.dump(state, f)


# ---------------------------------------------------------------------------
# Question selection
# ---------------------------------------------------------------------------

def _pick_new_question(questions, recent_indices, max_recent_ratio=0.4):
    """
    Pick a random question index, avoiding recently shown ones when possible.
    Clears the recency buffer when it would exclude too many questions.
    """
    n = len(questions)
    # Avoid the last N indices proportionally (up to 40% of pool)
    max_recent = max(1, int(n * max_recent_ratio))
    exclude = set(recent_indices[-max_recent:]) if len(recent_indices) >= max_recent else set(recent_indices)
    pool = [i for i in range(n) if i not in exclude]
    if not pool:
        pool = list(range(n))   # all excluded — pick from full pool
    return random.choice(pool)


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

    # Footer
    footer = f"Changes every {interval_minutes} min"
    ff = _load_font(13)
    fw = draw.textbbox((0, 0), footer, font=ff)[2]
    draw.text((width - margin - fw, height - 18), footer, fill=(190, 190, 190), font=ff)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path)
    print(f"[questions] Saved (topic={topic!r}, font={font_size}px)")
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
    state_file = cfg.get("state_file", STATE_FILE)
    interval_minutes = int(cfg.get("interval_minutes", 15))
    interval_seconds = interval_minutes * 60
    csv_path = cfg.get("csv_file", "data/questions/eink_facts.csv")
    force_new = bool(cfg.get("force_new", False))
    width = config.get("width", 800)
    height = config.get("height", 480)

    questions = _load_questions(csv_path)
    if not questions:
        return _render_fallback(output_path)

    state = _load_state(state_file)
    now = time.time()
    last_updated = state.get("last_updated", 0)
    current_index = state.get("current_index", -1)
    recent_indices = state.get("recent_indices", [])
    elapsed = now - last_updated

    # Pick a new question if: never picked, interval elapsed, or force_new
    need_new = (
        current_index < 0
        or current_index >= len(questions)
        or elapsed >= interval_seconds
        or force_new
    )

    if need_new:
        current_index = _pick_new_question(questions, recent_indices)
        recent_indices.append(current_index)
        # Keep the recency buffer bounded
        if len(recent_indices) > max(10, len(questions) // 2):
            recent_indices = recent_indices[-(len(questions) // 2):]
        state = {
            "current_index": current_index,
            "last_updated": now,
            "recent_indices": recent_indices,
        }
        _save_state(state_file, state)

        # Clear force_new so it doesn't keep forcing on every call
        if force_new:
            try:
                import sys, os as _os
                bot_state_path = _os.path.join(
                    _os.path.dirname(_os.path.abspath("config.yml")), "bot_state.json"
                )
                if _os.path.exists(bot_state_path):
                    import json as _json
                    with open(bot_state_path) as _f:
                        bs = _json.load(_f)
                    if bs.get("questions", {}).get("force_new"):
                        bs.setdefault("questions", {})["force_new"] = False
                        with open(bot_state_path, "w") as _f:
                            _json.dump(bs, _f, indent=2)
            except Exception:
                pass

        reason = "force_new" if force_new else ("first run" if last_updated == 0 else f"interval ({elapsed/60:.1f} min elapsed)")
        print(f"[questions] New question #{current_index} ({reason})")
    else:
        remaining = (interval_seconds - elapsed) / 60
        print(f"[questions] Showing question #{current_index} ({remaining:.1f} min remaining)")

    topic, question = questions[current_index]
    return _render(topic, question, interval_minutes, output_path, width, height)


if __name__ == "__main__":
    import yaml
    with open("config.yml") as f:
        cfg = yaml.safe_load(f)
    path = generate(cfg)
    print(f"Output: {path}")
