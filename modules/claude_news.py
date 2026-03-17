"""
Claude Code News module.

Fetches the 3 most recent Claude Code feature releases from the official
changelog and renders them on an 800×480 e-ink display.

Primary source: docs.anthropic.com/en/docs/claude-code/changelog
Fallback:       registry.npmjs.org/@anthropic-ai/claude-code (version + date only)
"""

import html as _html
import json
import os
import platform
import re
import time
from html.parser import HTMLParser

import requests
from PIL import Image, ImageDraw, ImageFont


HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EinkDisplay/1.0)"}
CACHE_DIR = "data"
CACHE_TTL = 3600  # 1 hour

CHANGELOG_URL = "https://docs.anthropic.com/en/docs/claude-code/changelog"
NPM_URL = "https://registry.npmjs.org/@anthropic-ai/claude-code"
MAX_RELEASES = 3


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
# Changelog HTML parser
# ---------------------------------------------------------------------------

class _ChangelogParser(HTMLParser):
    """Extract version sections and their bullet-point features."""

    def __init__(self):
        super().__init__()
        self.releases = []
        self._current = None      # active release dict
        self._capture = False
        self._in_li = False
        self._buf = ""

    def handle_starttag(self, tag, attrs):
        if tag in ("h1", "h2", "h3"):
            self._capture = True
            self._buf = ""
        elif tag == "li":
            self._in_li = True
            self._capture = True
            self._buf = ""

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3") and self._capture:
            text = _html.unescape(self._buf.strip())
            # Match version numbers like "1.2.3", "v1.2.3", or date-like headings
            if re.search(r'\d+\.\d+', text):
                if len(self.releases) < MAX_RELEASES:
                    self._current = {"version": text, "features": []}
                    self.releases.append(self._current)
                else:
                    self._current = None
            self._capture = False
            self._buf = ""

        elif tag == "li" and self._in_li:
            text = _html.unescape(self._buf.strip())
            if text and self._current and len(self._current["features"]) < 4:
                self._current["features"].append(text)
            self._in_li = False
            self._capture = False
            self._buf = ""

    def handle_data(self, data):
        if self._capture:
            self._buf += data


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def _fetch_changelog():
    """Fetch releases from the Anthropic docs changelog page."""
    try:
        resp = requests.get(CHANGELOG_URL, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        parser = _ChangelogParser()
        parser.feed(resp.text)
        releases = [r for r in parser.releases if r.get("features")]
        if releases:
            print(f"[claude_news] Parsed {len(releases)} releases from changelog page")
            return releases[:MAX_RELEASES]
    except Exception as e:
        print(f"[claude_news] Changelog fetch failed: {e}")
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
            {"version": f"v{v}", "features": [f"Released {t[:10]}"]}
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
            "features": ["Visit docs.anthropic.com/en/docs/claude-code/changelog for the latest updates."],
        }
    ]
    _save_cache(releases)
    return releases


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

def _render(releases, output_path, width=800, height=480):
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    margin = 22
    content_w = width - 2 * margin
    y = 16

    # ── Header ──────────────────────────────────────────────────────────────
    hf = _font(36)
    draw.text((margin, y), "Claude Code", fill=(20, 20, 20), font=hf)
    header_h = draw.textbbox((0, 0), "Claude Code", font=hf)[3]

    sub_f = _font(17)
    sub = "What's New"
    sub_w = draw.textbbox((0, 0), sub, font=sub_f)[2]
    draw.text((width - margin - sub_w, y + header_h - draw.textbbox((0, 0), sub, font=sub_f)[3] - 2),
              sub, fill=(130, 130, 130), font=sub_f)

    y += header_h + 8
    draw.line([(margin, y), (width - margin, y)], fill=(190, 190, 190), width=1)
    y += 10

    # ── Release entries ──────────────────────────────────────────────────────
    vf = _font(19)
    ff = _font(15)
    line_gap = 3

    for idx, release in enumerate(releases[:MAX_RELEASES]):
        if y > height - 36:
            break

        version = release.get("version", "").strip()
        features = [f.strip() for f in release.get("features", []) if f.strip()]

        # Version label
        draw.text((margin, y), version, fill=(25, 25, 25), font=vf)
        y += draw.textbbox((0, 0), version, font=vf)[3] + 4

        for feat in features[:3]:
            if y > height - 28:
                break
            wrapped = _wrap(f"• {feat}", ff, draw, content_w - 8)
            for line in wrapped.split("\n"):
                if y > height - 28:
                    break
                draw.text((margin + 8, y), line, fill=(55, 55, 55), font=ff)
                y += draw.textbbox((0, 0), line, font=ff)[3] + line_gap
            y += 2

        y += 6
        if idx < len(releases) - 1 and y < height - 36:
            draw.line([(margin, y), (width - margin, y)], fill=(215, 215, 215), width=1)
            y += 8

    # ── Footer ───────────────────────────────────────────────────────────────
    af = _font(11)
    attr = "docs.anthropic.com/en/docs/claude-code/changelog"
    aw = draw.textbbox((0, 0), attr, font=af)[2]
    draw.text((width - margin - aw, height - 14), attr, fill=(185, 185, 185), font=af)

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
