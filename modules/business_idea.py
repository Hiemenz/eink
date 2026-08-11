"""
Business Idea module.

Scans the sibling `business-ideas` repo for markdown idea files and renders
the newest not-yet-shown idea as an 800x480 BMP. That repo accumulates ideas
as markdown "session" logs (several numbered ideas per file) and standalone
one-idea-per-file proof-of-concepts — this module reads them directly rather
than any particular data file, so anything new written there shows up here.

This module only reads from the source repo — it never writes to it. Which
ideas have already been shown is tracked separately in this project's own
data/ directory.

Config section (add to config.yml):
  business_idea:
    output_path: images/business_idea.bmp
    source_dir: ../business-ideas
    idea_dirs: [main-income-ideas, side-income-ideas, discovered-problems, proof-of-concepts]
    shown_cache: data/business_idea_shown.json
    update_interval: 86400   # seconds — how often to advance to a new idea
"""

import glob
import hashlib
import json
import os
import re
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import get_font, get_logger

log = get_logger("business_idea")

WIDTH, HEIGHT = 800, 480

DEFAULT_SOURCE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "business-ideas",
)
DEFAULT_IDEA_DIRS = [
    "main-income-ideas", "side-income-ideas", "discovered-problems", "proof-of-concepts",
]
DEFAULT_SHOWN_CACHE = "data/business_idea_shown.json"

CATEGORY_LABELS = {
    "main-income-ideas": "Main Income Idea",
    "side-income-ideas": "Side Income Idea",
    "discovered-problems": "Discovered Problem",
    "proof-of-concepts": "Proof of Concept",
}

_NUMBERED_HEADER = re.compile(r"^#{2,3}\s*\d+\.\s*(.+)$")
_DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_METADATA_LINE = re.compile(r"^\*\*(Date|Fit):?\*\*", re.IGNORECASE)

MAX_DETAIL_CHARS = 520


# ---------------------------------------------------------------------------
# Markdown parsing
# ---------------------------------------------------------------------------


def _clean(text):
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\*(.*?)\*", r"\1", text)
    text = re.sub(r"^[-#]+\s*", "", text.strip())
    return text.strip("[] \t")


def _numbered_sections(text):
    """Split markdown on '### N. Title' / '## N. Title' headers. Empty if none found."""
    lines = text.splitlines()
    sections = []
    title, body = None, []
    for line in lines:
        m = _NUMBERED_HEADER.match(line.strip())
        if m:
            if title is not None:
                sections.append((title, "\n".join(body).strip()))
            title = _clean(m.group(1))
            body = []
        elif title is not None:
            body.append(line)
    if title is not None:
        sections.append((title, "\n".join(body).strip()))
    return sections


def _whole_file_section(text):
    """Treat an entire file as one idea, titled by its first '# ' heading."""
    lines = text.splitlines()
    title = None
    body_start = 0
    for i, line in enumerate(lines):
        if line.strip().startswith("# "):
            title = _clean(line.strip())
            body_start = i + 1
            break
    if title is None:
        return []
    return [(title, "\n".join(lines[body_start:]).strip())]


def _sections_from_file(text):
    sections = _numbered_sections(text)
    return sections if sections else _whole_file_section(text)


def _extract_detail(body):
    """Pull the first substantial prose paragraph out of an idea body, cleaned and capped."""
    paragraphs = []
    current = []
    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith("---"):
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        if s.startswith("#"):
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        if _METADATA_LINE.match(s):
            continue
        current.append(_clean(s))
    if current:
        paragraphs.append(" ".join(current))

    detail = next((p for p in paragraphs if len(p) > 40), (paragraphs[0] if paragraphs else ""))
    if len(detail) > MAX_DETAIL_CHARS:
        cutoff = detail.rfind(". ", 0, MAX_DETAIL_CHARS)
        detail = (detail[:cutoff + 1] if cutoff > 0 else detail[:MAX_DETAIL_CHARS].rsplit(" ", 1)[0]) + "…"
    return detail


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _discover_ideas(source_dir, idea_dirs):
    """Scan configured subdirectories for markdown files and flatten into an idea list."""
    ideas = []
    for idea_dir in idea_dirs:
        dir_path = os.path.join(source_dir, idea_dir)
        if not os.path.isdir(dir_path):
            continue
        for md_path in sorted(glob.glob(os.path.join(dir_path, "*.md"))):
            fname = os.path.basename(md_path)
            try:
                with open(md_path, encoding="utf-8") as f:
                    text = f.read()
                mtime = os.path.getmtime(md_path)
            except OSError as exc:
                log.warning("Failed to read %s: %s", md_path, exc)
                continue

            date_match = _DATE_PREFIX.match(fname)
            date_str = date_match.group(1) if date_match else None

            for order, (title, body) in enumerate(_sections_from_file(text)):
                if not title or not body:
                    continue
                idea_id = hashlib.md5(f"{idea_dir}/{fname}::{title}".encode()).hexdigest()
                ideas.append({
                    "id": idea_id,
                    "title": title,
                    "body": body,
                    "category": CATEGORY_LABELS.get(idea_dir, idea_dir),
                    "date": date_str,
                    "mtime": mtime,
                    "order": order,
                })
    log.info("Discovered %d idea(s) across %s", len(ideas), idea_dirs)
    return ideas


def _load_shown(shown_cache):
    if os.path.exists(shown_cache):
        try:
            with open(shown_cache) as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()


def _save_shown(shown_cache, shown):
    os.makedirs(os.path.dirname(shown_cache) or ".", exist_ok=True)
    with open(shown_cache, "w") as f:
        json.dump(sorted(shown), f)


def _pick_idea(ideas, shown_cache):
    """Pick the newest not-yet-shown idea, cycling back once all have been seen."""
    if not ideas:
        return None

    shown = _load_shown(shown_cache)
    unseen = [i for i in ideas if i["id"] not in shown]
    if not unseen:
        log.info("All ideas shown — resetting rotation.")
        shown = set()
        unseen = ideas

    unseen.sort(key=lambda i: (-i["mtime"], i["order"]))
    chosen = unseen[0]

    shown.add(chosen["id"])
    _save_shown(shown_cache, shown)

    return chosen


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _wrap(draw, text, font, max_width):
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
    return draw.textbbox((0, 0), "Ag", font=font)[3] + 4


def _tw(draw, text, font):
    return draw.textbbox((0, 0), text, font=font)[2]


def _render(idea, headline, detail, output_path, config):
    img = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(img)
    margin = 40
    text_w = WIDTH - 2 * margin
    y = 10

    # Header
    hdr_font = get_font(15, bold=False, config=config)
    hdr_text = "Business Idea"
    draw.text(((WIDTH - _tw(draw, hdr_text, hdr_font)) // 2, y),
              hdr_text, fill=(160, 160, 160), font=hdr_font)
    y += _lh(draw, hdr_font) + 2

    # Category / date tag line
    tags = " · ".join(t for t in (idea.get("category"), idea.get("date")) if t)
    if tags:
        tag_font = get_font(14, bold=False, config=config)
        draw.text(((WIDTH - _tw(draw, tags, tag_font)) // 2, y),
                  tags, fill=(140, 140, 140), font=tag_font)
        y += _lh(draw, tag_font) + 4

    draw.line([(margin, y), (WIDTH - margin, y)], fill=(200, 200, 200), width=1)
    y += 14

    footer_font = get_font(12, bold=False, config=config)
    footer_h = _lh(draw, footer_font) + 8

    details_reserve = int((HEIGHT - y - footer_h) * 0.5) if detail else 0
    headline_zone_h = HEIGHT - y - footer_h - details_reserve - 10

    headline_font = None
    headline_lines = None
    for size in range(36, 15, -2):
        f = get_font(size, bold=True, config=config)
        lines = _wrap(draw, headline, f, text_w)
        total_h = _lh(draw, f) * len(lines)
        if total_h <= headline_zone_h:
            headline_font = f
            headline_lines = lines
            break
    if headline_font is None:
        headline_font = get_font(16, bold=True, config=config)
        headline_lines = _wrap(draw, headline, headline_font, text_w)

    lh = _lh(draw, headline_font)
    total_h = lh * len(headline_lines)
    ty = y + max(0, (headline_zone_h - total_h) // 2)
    for line in headline_lines:
        draw.text(((WIDTH - _tw(draw, line, headline_font)) // 2, ty),
                  line, fill="black", font=headline_font)
        ty += lh
    y += headline_zone_h

    if detail:
        draw.line([(margin, y), (WIDTH - margin, y)], fill=(200, 200, 200), width=1)
        y += 10

        detail_font = get_font(12, bold=False, config=config)
        for size in range(15, 10, -1):
            f = get_font(size, bold=False, config=config)
            dlh = _lh(draw, f)
            total = len(_wrap(draw, detail, f, text_w)) * dlh
            if total <= details_reserve:
                detail_font = f
                break

        dlh = _lh(draw, detail_font)
        for line in _wrap(draw, detail, detail_font, text_w):
            if y + dlh > HEIGHT - footer_h:
                break
            draw.text((margin, y), line, fill=(60, 60, 60), font=detail_font)
            y += dlh

    attr_text = "business-ideas"
    aw = _tw(draw, attr_text, footer_font)
    draw.text((WIDTH - margin - aw, HEIGHT - _lh(draw, footer_font) - 4),
              attr_text, fill=(180, 180, 180), font=footer_font)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path)
    log.info("Saved business idea image to %s", output_path)
    return output_path


def _render_empty(output_path, config, source_dir):
    img = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(img)
    font = get_font(20, bold=True, config=config)
    sub_font = get_font(14, bold=False, config=config)

    msg = "No business ideas found"
    draw.text(((WIDTH - _tw(draw, msg, font)) // 2, HEIGHT // 2 - 30),
              msg, fill="black", font=font)
    sub = f"No markdown idea files found under {source_dir}"
    draw.text(((WIDTH - _tw(draw, sub, sub_font)) // 2, HEIGHT // 2 + 10),
              sub, fill=(140, 140, 140), font=sub_font)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path)
    return output_path


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def generate(config):
    """
    Generate a Business Idea image. Return output BMP file path.

    Config section (business_idea in config.yml):
      business_idea:
        output_path: images/business_idea.bmp
        source_dir: ../business-ideas
        idea_dirs: [main-income-ideas, side-income-ideas, discovered-problems, proof-of-concepts]
        shown_cache: data/business_idea_shown.json
        update_interval: 86400   # seconds — how often to advance to a new idea
    """
    cfg = config.get("business_idea", {})
    output_path = cfg.get("output_path", "images/business_idea.bmp")
    source_dir = cfg.get("source_dir", DEFAULT_SOURCE_DIR)
    idea_dirs = cfg.get("idea_dirs", DEFAULT_IDEA_DIRS)
    shown_cache = cfg.get("shown_cache", DEFAULT_SHOWN_CACHE)

    ideas = _discover_ideas(source_dir, idea_dirs)
    idea = _pick_idea(ideas, shown_cache)

    if idea is None:
        return _render_empty(output_path, config, source_dir)

    detail = _extract_detail(idea["body"])
    return _render(idea, idea["title"], detail, output_path, config)


if __name__ == "__main__":
    import yaml

    with open("config.yml") as fh:
        cfg = yaml.safe_load(fh)
    path = generate(cfg)
    print(f"Output: {path}")
