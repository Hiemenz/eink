"""
Claude Code News module.

Fetches the 5 most recent Claude Code releases from the official GitHub
CHANGELOG.md and renders them on an 800×480 e-ink display.

Primary source: raw.githubusercontent.com/anthropics/claude-code/refs/heads/main/CHANGELOG.md
Fallback:       registry.npmjs.org/@anthropic-ai/claude-code (version + date only)
"""

import json
import os
import platform
import re
import time

import requests
from PIL import Image, ImageDraw, ImageFont


HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EinkDisplay/1.0)"}
CACHE_DIR = "data"
CACHE_TTL = 3600  # 1 hour

CHANGELOG_URL = "https://raw.githubusercontent.com/anthropics/claude-code/refs/heads/main/CHANGELOG.md"
NPM_URL = "https://registry.npmjs.org/@anthropic-ai/claude-code"
MAX_RELEASES = 5
MAX_ITEMS = 3   # bullet points shown per release


# ---------------------------------------------------------------------------
# Font helpers
# ---------------------------------------------------------------------------

def _font_path():
    if platform.system() == "Darwin":
        return "/Library/Fonts/Arial Unicode.ttf"
    return "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"


def _font(size):
    try:
        return ImageFont.truetype(_font_path(), size)
    except Exception:
        return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _cache_path():
    return os.path.join(CACHE_DIR, "claude_news_cache.json")


def _load_cache():
    path = _cache_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        if time.time() - data.get("fetched_at", 0) > CACHE_TTL:
            return None
        return data.get("releases")
    except Exception:
        return None


def _save_cache(releases):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(), "w") as f:
        json.dump({"fetched_at": time.time(), "releases": releases}, f)


# ---------------------------------------------------------------------------
# Markdown parser
# ---------------------------------------------------------------------------

def _parse_changelog(text: str) -> list:
    """Parse CHANGELOG.md into a list of {version, items} dicts."""
    releases = []
    current = None

    for line in text.splitlines():
        # Version heading: ## 2.1.77  or  ## [2.1.77]
        m = re.match(r'^##\s+\[?(\d+\.\d+[\d.]*)\]?', line)
        if m:
            if len(releases) >= MAX_RELEASES:
                break
            current = {"version": f"v{m.group(1)}", "items": []}
            releases.append(current)
            continue

        if current is None:
            continue

        # Bullet: "- Some feature text"
        m = re.match(r'^[-*]\s+(.*)', line)
        if m and len(current["items"]) < MAX_ITEMS:
            text_content = m.group(1).strip()
            # Strip markdown bold/italic/backticks for cleaner display
            text_content = re.sub(r'[`*_]', '', text_content)
            if text_content:
                current["items"].append(text_content)

    return releases


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def _fetch_changelog():
    """Fetch and parse the raw CHANGELOG.md from GitHub."""
    try:
        resp = requests.get(CHANGELOG_URL, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        releases = _parse_changelog(resp.text)
        releases = [r for r in releases if r.get("items")]
        if releases:
            print(f"[claude_news] Parsed {len(releases)} releases from CHANGELOG.md")
            return releases
    except Exception as e:
        print(f"[claude_news] CHANGELOG.md fetch failed: {e}")
    return None


def _fetch_npm_fallback():
    """Fall back to npm registry for version + release date."""
    try:
        resp = requests.get(NPM_URL, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        times = data.get("time", {})
        versions = sorted(
            [(v, t) for v, t in times.items() if re.match(r'^\d+\.\d+\.\d+$', v)],
            key=lambda x: x[1],
            reverse=True,
        )[:MAX_RELEASES]
        releases = [
            {"version": f"v{v}", "items": [f"Released {t[:10]}"]}
            for v, t in versions
        ]
        print(f"[claude_news] npm fallback: {[r['version'] for r in releases]}")
        return releases or None
    except Exception as e:
        print(f"[claude_news] npm fetch failed: {e}")
    return None


def _get_releases():
    cached = _load_cache()
    if cached:
        return cached
    releases = _fetch_changelog() or _fetch_npm_fallback() or [
        {
            "version": "Claude Code",
            "items": ["Visit docs.anthropic.com/en/docs/claude-code/changelog"],
        }
    ]
    _save_cache(releases)
    return releases


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _truncate(text, font, draw, max_width):
    """Truncate text with ellipsis to fit max_width."""
    if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
        return text
    while text:
        text = text[:-1]
        if draw.textbbox((0, 0), text + "…", font=font)[2] <= max_width:
            return text + "…"
    return "…"


def _render(releases, output_path, width=800, height=480):
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    margin = 18
    content_w = width - 2 * margin
    y = 10

    # ── Header ──────────────────────────────────────────────────────────────
    hf = _font(28)
    draw.text((margin, y), "Claude Code", fill=(20, 20, 20), font=hf)
    header_h = draw.textbbox((0, 0), "Claude Code", font=hf)[3]

    sub_f = _font(14)
    sub = "What's New"
    sub_w = draw.textbbox((0, 0), sub, font=sub_f)[2]
    sub_h = draw.textbbox((0, 0), sub, font=sub_f)[3]
    draw.text((width - margin - sub_w, y + header_h - sub_h),
              sub, fill=(140, 140, 140), font=sub_f)

    y += header_h + 6
    draw.line([(margin, y), (width - margin, y)], fill=(190, 190, 190), width=1)
    y += 7

    # ── Release entries ──────────────────────────────────────────────────────
    vf = _font(15)
    ff = _font(12)
    line_gap = 2
    footer_reserve = 16
    available = height - footer_reserve

    for idx, release in enumerate(releases[:MAX_RELEASES]):
        if y > available - 20:
            break

        version = release.get("version", "").strip()
        items = release.get("items", [])

        # Version label
        draw.text((margin, y), version, fill=(20, 20, 20), font=vf)
        y += draw.textbbox((0, 0), version, font=vf)[3] + 2

        for item in items[:MAX_ITEMS]:
            if y > available - 14:
                break
            item = item.strip()
            if not item:
                continue
            line = _truncate("• " + item, ff, draw, content_w - 6)
            draw.text((margin + 4, y), line, fill=(60, 60, 60), font=ff)
            y += draw.textbbox((0, 0), line, font=ff)[3] + line_gap

        y += 4
        if idx < len(releases) - 1 and y < available - 20:
            draw.line([(margin, y), (width - margin, y)], fill=(220, 220, 220), width=1)
            y += 5

    # ── Footer ───────────────────────────────────────────────────────────────
    af = _font(10)
    attr = "docs.anthropic.com/en/docs/claude-code/changelog"
    aw = draw.textbbox((0, 0), attr, font=af)[2]
    draw.text((width - margin - aw, height - 13), attr, fill=(190, 190, 190), font=af)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path)
    print(f"[claude_news] Saved to {output_path}")
    return output_path


def _render_fallback(output_path):
    img = Image.new("RGB", (800, 480), "white")
    draw = ImageDraw.Draw(img)
    draw.text((22, 180), "Claude Code News", fill=(30, 30, 30), font=_font(30))
    draw.text((22, 228), "Unable to fetch changelog.", fill=(120, 120, 120), font=_font(19))
    draw.text((22, 256), "Check docs.anthropic.com for updates.", fill=(140, 140, 140), font=_font(17))
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path)
    return output_path


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def generate(config):
    """Generate Claude Code news image. Return output path."""
    cfg = config.get("claude_news", {})
    output_path = cfg.get("output_path", "images/claude_news.bmp")
    width = config.get("width", 800)
    height = config.get("height", 480)

    try:
        releases = _get_releases()
        return _render(releases, output_path, width, height)
    except Exception as e:
        print(f"[claude_news] Render error: {e}")
        return _render_fallback(output_path)


if __name__ == "__main__":
    import yaml
    with open("config.yml") as f:
        cfg = yaml.safe_load(f)
    path = generate(cfg)
    print(f"Output: {path}")
