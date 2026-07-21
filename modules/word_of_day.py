"""
Word of the Day module.

Fetches the Merriam-Webster Word of the Day and renders it as an
800×480 BMP on a white background.

Data sources (in priority order):
  1. M-W WOTD RSS feed (word extraction)
  2. Deterministic daily word from built-in list (RSS failure fallback)
  3. Merriam-Webster Collegiate Dictionary API (definitions, if api_key set)
  4. Hardcoded definitions for the most common fallback words (no-key fallback)

Cache: data/wotd_cache_{YYYY-MM-DD}.json  (one file per day)

Config section (add to config.yml):
  word_of_day:
    output_path: images/wotd_display.bmp
    api_key: ""          # free key from dictionaryapi.com
    cache_dir: data/

Layout (800×480):
  - "Word of the Day" header, small gray, centered
  - Thin horizontal rule
  - Word in large bold text, centered, auto-sized 72–96px
  - Pronunciation below word in gray (if available from API), size 18
  - Part of speech in gray, centered, size 16
  - Thin horizontal rule
  - Up to 3 numbered definitions, left-aligned, auto-sized 14–18px
  - Bottom-right attribution: "merriam-webster.com", tiny gray
"""

import json
import os
import random
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date

import requests
from PIL import Image, ImageDraw

# Ensure project root is on the path when this file is run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import get_font, get_logger

log = get_logger("wotd")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WIDTH, HEIGHT = 800, 480
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EinkDisplay/1.0)"}

RSS_URL = "https://www.merriam-webster.com/wotd/feed/rss2"
MW_API_URL = "https://www.dictionaryapi.com/api/v3/references/collegiate/json/{word}?key={key}"

FALLBACK_WORDS = [
    "ephemeral", "sanguine", "ineffable", "mellifluous", "serendipity",
    "loquacious", "sycophant", "limpid", "perspicacious", "obstinate",
    "ubiquitous", "pellucid", "pulchritudinous", "tenacious", "laconic",
    "verbose", "esoteric", "recondite", "insouciant", "perfidious",
    "mendacious", "ebullient", "taciturn", "garrulous", "mercurial",
    "phlegmatic", "choleric", "furtive", "truculent", "magnanimous",
    "pusillanimous", "equivocate", "obsequious", "perspicuous", "lucid",
    "cogent", "dilatory", "punctilious", "indefatigable", "intrepid",
    "sedulous", "fastidious", "querulous", "mawkish", "maudlin",
    "jejune", "bathetic", "anodyne", "shibboleth", "sophistry",
]

# Hardcoded definitions for the most common fallback words (used when no API key)
CANNED_DEFS = {
    "ephemeral":     ("adjective", ["lasting for only a short time; transitory."]),
    "sanguine":      ("adjective", ["optimistic, especially in a difficult situation."]),
    "ineffable":     ("adjective", ["too great or extreme to be expressed in words."]),
    "mellifluous":   ("adjective", ["sweet or musical; pleasant to hear."]),
    "serendipity":   ("noun",      ["the occurrence of events by chance in a happy way."]),
    "loquacious":    ("adjective", ["tending to talk a great deal; garrulous."]),
    "sycophant":     ("noun",      ["a person who acts obsequiously toward someone in order to gain advantage."]),
    "limpid":        ("adjective", ["completely clear and transparent."]),
    "perspicacious": ("adjective", ["having a ready insight into things; shrewd."]),
    "obstinate":     ("adjective", ["stubbornly refusing to change one's opinion or course of action."]),
}

# ---------------------------------------------------------------------------
# Word acquisition
# ---------------------------------------------------------------------------


def _daily_fallback_word():
    """Pick a deterministic word for today from the built-in list."""
    seed = int(date.today().strftime("%Y%m%d"))
    return random.Random(seed).choice(FALLBACK_WORDS)


def _fetch_rss_word():
    """Return the M-W WOTD from the RSS feed, or None on failure."""
    try:
        resp = requests.get(RSS_URL, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        items = root.findall(".//item")
        if not items:
            return None
        title = items[0].findtext("title", "")
        # Title format: "ephemeral : meaning and usage" or "ephemeral"
        word = title.split(":")[0].strip().lower()
        if word:
            log.info("RSS WOTD: %s", word)
            return word
    except Exception as exc:
        log.warning("RSS fetch failed: %s", exc)
    return None


# ---------------------------------------------------------------------------
# Definition lookup
# ---------------------------------------------------------------------------


def _fetch_mw_definition(word, api_key):
    """
    Fetch definition data from the M-W Collegiate Dictionary API.

    Returns a dict:
      {word, pos, pronunciation, definitions: [str, ...]}
    or None on failure / unexpected response.
    """
    url = MW_API_URL.format(word=word, key=api_key)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("M-W API request failed: %s", exc)
        return None

    if not data or not isinstance(data, list) or not isinstance(data[0], dict):
        log.warning("M-W API returned unexpected format for '%s'", word)
        return None

    entry = data[0]

    # Headword: strip asterisks, prefer hwi.hw then meta.id
    hwi = entry.get("hwi", {})
    if hwi.get("hw"):
        headword = hwi["hw"].replace("*", "")
    elif entry.get("meta", {}).get("id"):
        headword = entry["meta"]["id"].split(":")[0]
    else:
        headword = word

    # Part of speech
    pos = entry.get("fl", "")

    # Pronunciation — keep printable ASCII characters only for e-ink rendering
    pronunciation = ""
    prs = hwi.get("prs", [])
    if prs and prs[0].get("mw"):
        raw = prs[0]["mw"]
        pronunciation = re.sub(r"[^\x20-\x7E]", "", raw).strip()

    # Short definitions (up to 3)
    definitions = [d for d in entry.get("shortdef", []) if d][:3]

    return {
        "word": headword,
        "pos": pos,
        "pronunciation": pronunciation,
        "definitions": definitions,
    }


def _canned_definition(word):
    """Return a definition dict from hardcoded data, or a bare skeleton."""
    if word in CANNED_DEFS:
        pos, defs = CANNED_DEFS[word]
        return {"word": word, "pos": pos, "pronunciation": "", "definitions": defs}
    return {"word": word, "pos": "", "pronunciation": "", "definitions": []}


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def _cache_path(cache_dir):
    return os.path.join(cache_dir, f"wotd_cache_{date.today().isoformat()}.json")


def _load_cache(cache_dir):
    path = _cache_path(cache_dir)
    if os.path.exists(path):
        try:
            with open(path) as fh:
                data = json.load(fh)
            log.info("Loaded WOTD cache from %s", path)
            return data
        except Exception as exc:
            log.warning("Cache read failed: %s", exc)
    return None


def _save_cache(data, cache_dir):
    os.makedirs(cache_dir, exist_ok=True)
    path = _cache_path(cache_dir)
    try:
        with open(path, "w") as fh:
            json.dump(data, fh)
        log.info("WOTD cache saved to %s", path)
    except Exception as exc:
        log.warning("Cache write failed: %s", exc)


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _wrap(draw, text, font, max_width):
    """Word-wrap *text* to fit *max_width*, returning a list of lines."""
    words = text.split()
    lines, current = [], []
    for word in words:
        test = " ".join(current + [word])
        if draw.textbbox((0, 0), test, font=font)[2] <= max_width:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines or [""]


def _lh(draw, font):
    """Line height with a small leading gap."""
    return draw.textbbox((0, 0), "Ag", font=font)[3] + 4


def _tw(draw, text, font):
    return draw.textbbox((0, 0), text, font=font)[2]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _render(word_data, output_path, config):
    word         = word_data.get("word", "")
    pos          = word_data.get("pos", "")
    pronunciation = word_data.get("pronunciation", "")
    definitions  = word_data.get("definitions", [])

    img  = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(img)

    margin     = 40
    def_margin = 60
    text_w     = WIDTH - 2 * margin

    y = 10

    # ---- Header: "Word of the Day" ----
    hdr_font = get_font(15, bold=False, config=config)
    hdr_text = "Word of the Day"
    draw.text(((WIDTH - _tw(draw, hdr_text, hdr_font)) // 2, y),
              hdr_text, fill=(160, 160, 160), font=hdr_font)
    y += _lh(draw, hdr_font) + 4

    # Thin horizontal rule
    draw.line([(margin, y), (WIDTH - margin, y)], fill=(200, 200, 200), width=1)
    y += 10

    # ---- Word (large bold, auto-size 72–96px) ----
    word_font = None
    for size in range(96, 70, -2):
        f = get_font(size, bold=True, config=config)
        if _tw(draw, word, f) <= text_w:
            word_font = f
            break
    if word_font is None:
        word_font = get_font(72, bold=True, config=config)

    draw.text(((WIDTH - _tw(draw, word, word_font)) // 2, y),
              word, fill="black", font=word_font)
    y += _lh(draw, word_font) + 2

    # ---- Pronunciation ----
    if pronunciation:
        pron_font = get_font(18, bold=False, config=config)
        pron_text = f"/{pronunciation}/"
        draw.text(((WIDTH - _tw(draw, pron_text, pron_font)) // 2, y),
                  pron_text, fill=(140, 140, 140), font=pron_font)
        y += _lh(draw, pron_font) + 2

    # ---- Part of speech ----
    if pos:
        pos_font = get_font(16, bold=False, config=config)
        draw.text(((WIDTH - _tw(draw, pos, pos_font)) // 2, y),
                  pos, fill=(140, 140, 140), font=pos_font)
        y += _lh(draw, pos_font) + 4

    # Thin horizontal rule
    draw.line([(margin, y), (WIDTH - margin, y)], fill=(200, 200, 200), width=1)
    y += 10

    # ---- Definitions ----
    attr_font    = get_font(11, bold=False, config=config)
    bottom_reserve = _lh(draw, attr_font) + 8
    available_h  = HEIGHT - y - bottom_reserve
    def_w        = WIDTH - def_margin - margin

    if definitions:
        # Auto-size definitions font: start at 18, min 14
        def_font = get_font(14, bold=False, config=config)  # safe minimum
        for size in range(18, 13, -1):
            f   = get_font(size, bold=False, config=config)
            lh  = _lh(draw, f)
            total = sum(
                len(_wrap(draw, f"{i}.  {d}", f, def_w)) * lh + 4
                for i, d in enumerate(definitions[:3], 1)
            )
            if total <= available_h:
                def_font = f
                break

        lh = _lh(draw, def_font)
        for i, defn in enumerate(definitions[:3], 1):
            for line in _wrap(draw, f"{i}.  {defn}", def_font, def_w):
                if y + lh > HEIGHT - bottom_reserve:
                    break
                draw.text((def_margin, y), line, fill="black", font=def_font)
                y += lh
            y += 4
    else:
        no_def_font = get_font(16, bold=False, config=config)
        draw.text((def_margin, y), "No definition available.",
                  fill=(140, 140, 140), font=no_def_font)

    # ---- Attribution (bottom-right) ----
    attr_text = "merriam-webster.com"
    aw = _tw(draw, attr_text, attr_font)
    draw.text((WIDTH - margin - aw, HEIGHT - _lh(draw, attr_font) - 4),
              attr_text, fill=(180, 180, 180), font=attr_font)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path)
    log.info("Saved WOTD image to %s", output_path)
    return output_path


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def generate(config):
    """
    Generate Word of the Day image. Return output BMP file path.

    Config section (word_of_day in config.yml):
      word_of_day:
        output_path: images/wotd_display.bmp
        api_key: ""          # free key from dictionaryapi.com
        cache_dir: data/
    """
    cfg         = config.get("word_of_day", {})
    output_path = cfg.get("output_path", "images/wotd_display.bmp")
    api_key     = cfg.get("api_key", "").strip()
    cache_dir   = cfg.get("cache_dir", "data/")

    # Serve from cache if today's data is already on disk
    cached = _load_cache(cache_dir)
    if cached:
        return _render(cached, output_path, config)

    # Determine the word of the day
    word = _fetch_rss_word() or _daily_fallback_word()
    log.info("WOTD: %s", word)

    # Fetch definition (API or canned)
    word_data = None
    if api_key:
        word_data = _fetch_mw_definition(word, api_key)
        if not word_data:
            log.warning("API lookup failed for '%s'; falling back to canned definition", word)

    if not word_data:
        word_data = _canned_definition(word)

    _save_cache(word_data, cache_dir)
    return _render(word_data, output_path, config)


if __name__ == "__main__":
    import yaml

    with open("config.yml") as fh:
        cfg = yaml.safe_load(fh)
    path = generate(cfg)
    print(f"Output: {path}")
