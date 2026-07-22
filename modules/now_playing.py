"""
Now Playing / Recent Track module.

Displays the track you are currently listening to (or your most recently
scrobbled track) using the free Last.fm API. Renders large album art on the
left and track / artist / album metadata on the right, on an 800x480 canvas.

Data source: Last.fm user.getrecenttracks
    https://ws.audioscrobbler.com/2.0/?method=user.getrecenttracks
Get a free API key at https://www.last.fm/api

Responses are cached for 60 seconds in ``data/now_playing_cache.json`` and the
album art is cached in ``data/now_playing_art.png`` so transient network
failures reuse the last good result instead of blanking the display.

Config (config.yml):

    now_playing:
      output_path: images/now_playing.bmp
      api_key: ""          # free key from last.fm/api
      username: ""         # your last.fm username
      cache_dir: data/

States:
  * No api_key or username  -> friendly "Configure Last.fm" screen.
  * Fetch fails, no cache    -> "Now Playing unavailable" centered.
  * Otherwise                -> the now-playing / last-played card.
"""

import os
import sys
import json
import time
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from PIL import Image, ImageDraw

from utils import get_font, get_logger

logger = get_logger("now_playing")

WIDTH = 800
HEIGHT = 480

WHITE = (255, 255, 255)
BLACK = (20, 20, 20)
DARK = (30, 30, 30)
GRAY = (110, 110, 110)
LIGHT_GRAY = (170, 170, 170)
BORDER = (60, 60, 60)

LASTFM_URL = "https://ws.audioscrobbler.com/2.0/"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EinkDisplay/1.0)"}
CACHE_TTL = 60  # seconds

# Layout constants
ART_SIZE = 440
ART_MARGIN = 20
RIGHT_X = ART_MARGIN + ART_SIZE + 30  # left edge of the right-hand text panel
RIGHT_W = WIDTH - RIGHT_X - 24        # available width for right-hand text


# ── Data fetching ────────────────────────────────────────────────────────────
def _cache_paths(cache_dir):
    return (
        os.path.join(cache_dir, "now_playing_cache.json"),
        os.path.join(cache_dir, "now_playing_art.png"),
    )


def _read_cache(cache_json):
    try:
        with open(cache_json) as f:
            return json.load(f)
    except Exception:
        return None


def _largest_image_url(images):
    """Pick the largest non-empty album art URL from a Last.fm image list."""
    if not images:
        return None
    # Last.fm orders images small -> extralarge/mega; walk from the end.
    for entry in reversed(images):
        url = (entry or {}).get("#text", "").strip()
        if url:
            return url
    return None


def _fetch_track(api_key, username):
    """Fetch the most recent track from Last.fm. Returns a normalized dict or None."""
    params = {
        "method": "user.getrecenttracks",
        "user": username,
        "api_key": api_key,
        "format": "json",
        "limit": 1,
    }
    try:
        resp = requests.get(LASTFM_URL, params=params, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("Last.fm fetch failed: %s", e)
        return None

    if "error" in data:
        logger.warning("Last.fm API error %s: %s", data.get("error"), data.get("message"))
        return None

    tracks = (data.get("recenttracks", {}) or {}).get("track", [])
    if isinstance(tracks, dict):
        tracks = [tracks]
    if not tracks:
        logger.warning("Last.fm returned no tracks for user '%s'", username)
        return None

    track = tracks[0]
    attr = track.get("@attr", {}) or {}
    now_playing = str(attr.get("nowplaying", "")).lower() == "true"

    date_info = track.get("date", {}) or {}
    played_uts = None
    if not now_playing and date_info:
        try:
            played_uts = int(date_info.get("uts"))
        except (TypeError, ValueError):
            played_uts = None

    return {
        "name": track.get("name", "").strip() or "Unknown Track",
        "artist": (track.get("artist", {}) or {}).get("#text", "").strip() or "Unknown Artist",
        "album": (track.get("album", {}) or {}).get("#text", "").strip(),
        "image_url": _largest_image_url(track.get("image", [])),
        "nowplaying": now_playing,
        "played_uts": played_uts,
        "fetched_at": int(time.time()),
    }


def _download_art(url, art_path):
    """Download album art to art_path. Returns True on success."""
    if not url:
        return False
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content)).convert("RGB")
        img.save(art_path)
        return True
    except Exception as e:
        logger.warning("Album art download failed: %s", e)
        return False


def _get_track(api_key, username, cache_dir):
    """Return (track_dict, art_available). Uses a 60s cache and falls back to it."""
    cache_json, art_path = _cache_paths(cache_dir)
    cached = _read_cache(cache_json)

    # Serve fresh-enough cache without hitting the network.
    if cached and (int(time.time()) - cached.get("fetched_at", 0)) < CACHE_TTL:
        logger.info("Using cached Last.fm data (<%ss old)", CACHE_TTL)
        return cached, os.path.exists(art_path)

    track = _fetch_track(api_key, username)
    if track is None:
        if cached:
            logger.info("Fetch failed — reusing stale cached track")
            return cached, os.path.exists(art_path)
        return None, False

    # Only re-download art if the URL changed or the file is missing.
    art_url = track.get("image_url")
    need_download = bool(art_url) and (
        not cached
        or cached.get("image_url") != art_url
        or not os.path.exists(art_path)
    )
    if need_download:
        _download_art(art_url, art_path)

    try:
        with open(cache_json, "w") as f:
            json.dump(track, f)
    except Exception as e:
        logger.warning("Could not write cache: %s", e)

    return track, os.path.exists(art_path)


# ── Helpers ──────────────────────────────────────────────────────────────────
def _relative_time(played_uts):
    if not played_uts:
        return ""
    delta = int(time.time()) - int(played_uts)
    if delta < 60:
        return "just now"
    minutes = delta // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hr ago" if hours == 1 else f"{hours} hrs ago"
    days = hours // 24
    return f"{days} day ago" if days == 1 else f"{days} days ago"


def _wrap(draw, text, font, max_width, max_lines):
    """Word-wrap text to fit max_width, capped at max_lines (ellipsizing last)."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = (current + " " + word).strip()
        if draw.textbbox((0, 0), test, font=font)[2] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
    if current and len(lines) < max_lines:
        lines.append(current)

    # If we truncated, add an ellipsis to the final line.
    if len(lines) == max_lines and (len(" ".join(lines).split()) < len(words)):
        last = lines[-1]
        while last and draw.textbbox((0, 0), last + "…", font=font)[2] > max_width:
            last = last[:-1].rstrip()
        lines[-1] = last + "…"
    return lines


def _text_h(draw, font):
    return draw.textbbox((0, 0), "Ag", font=font)[3]


def _fit_title(draw, text, max_width, max_lines):
    """Pick the largest bold title font whose wrapped text fits max_lines."""
    for size in (46, 42, 38, 34, 30):
        font = get_font(size, bold=True)
        lines = _wrap(draw, text, font, max_width, max_lines)
        line_h = _text_h(draw, font) + 8
        # Confirm nothing was dropped beyond max_lines at this size.
        if len(lines) <= max_lines and lines and not lines[-1].endswith("…"):
            return font, lines, line_h
        if size == 30:
            return font, lines, line_h
    font = get_font(30, bold=True)
    return font, _wrap(draw, text, font, max_width, max_lines), _text_h(draw, font) + 8


# ── Rendering ────────────────────────────────────────────────────────────────
def _draw_placeholder_art(draw, x, y, size):
    """Draw a light box with a music note when no album art is available."""
    draw.rectangle([x, y, x + size, y + size], fill=(238, 238, 238), outline=BORDER, width=2)
    cx, cy = x + size // 2, y + size // 2
    r = size // 6
    # Two note heads
    draw.ellipse([cx - r - 10, cy + r, cx - 10, cy + 2 * r], fill=GRAY)
    draw.ellipse([cx + 30, cy + r - 6, cx + r + 40, cy + 2 * r - 6], fill=GRAY)
    # Stems
    stem_top = cy - r
    draw.rectangle([cx - 12, stem_top, cx - 8, cy + r + r // 2], fill=GRAY)
    draw.rectangle([cx + r + 38, stem_top - 6, cx + r + 42, cy + r + r // 2 - 6], fill=GRAY)
    # Beam joining the two stems
    draw.rectangle([cx - 12, stem_top, cx + r + 42, stem_top + 12], fill=GRAY)


def _paste_art(canvas, art_path, x, y, size):
    """Paste album art scaled to a centered square with a thin border."""
    try:
        art = Image.open(art_path).convert("RGB")
    except Exception as e:
        logger.warning("Could not open cached art: %s", e)
        return False
    # Cover-crop to square, then resize.
    side = min(art.width, art.height)
    left = (art.width - side) // 2
    top = (art.height - side) // 2
    art = art.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)
    canvas.paste(art, (x, y))
    ImageDraw.Draw(canvas).rectangle([x, y, x + size, y + size], outline=BORDER, width=2)
    return True


def _render_card(track, art_available, art_path, output_path, username):
    img = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(img)

    # Album art (left).
    art_y = (HEIGHT - ART_SIZE) // 2
    pasted = False
    if art_available:
        pasted = _paste_art(img, art_path, ART_MARGIN, art_y, ART_SIZE)
    if not pasted:
        _draw_placeholder_art(draw, ART_MARGIN, art_y, ART_SIZE)

    y = art_y  # align text panel roughly with the top of the art

    # Status line: badge for now-playing, else "LAST PLAYED" + relative time.
    badge_font = get_font(18, bold=True)
    if track.get("nowplaying"):
        label = "NOW PLAYING"
        tw = draw.textbbox((0, 0), label, font=badge_font)[2]
        bh = _text_h(draw, badge_font) + 12
        draw.rectangle([RIGHT_X, y, RIGHT_X + tw + 24, y + bh], fill=DARK)
        draw.text((RIGHT_X + 12, y + 6), label, fill=WHITE, font=badge_font)
    else:
        label = "LAST PLAYED"
        draw.text((RIGHT_X, y), label, fill=GRAY, font=badge_font)
        rel = _relative_time(track.get("played_uts"))
        if rel:
            lw = draw.textbbox((0, 0), label, font=badge_font)[2]
            small = get_font(18)
            draw.text((RIGHT_X + lw + 12, y), rel, fill=LIGHT_GRAY, font=small)
    y += 46

    # Track title — large bold, up to 3 lines.
    title_font, title_lines, title_lh = _fit_title(draw, track.get("name", ""), RIGHT_W, 3)
    for line in title_lines:
        draw.text((RIGHT_X, y), line, fill=BLACK, font=title_font)
        y += title_lh
    y += 14

    # Artist — medium gray, up to 2 lines.
    artist_font = get_font(30)
    for line in _wrap(draw, track.get("artist", ""), artist_font, RIGHT_W, 2):
        draw.text((RIGHT_X, y), line, fill=GRAY, font=artist_font)
        y += _text_h(draw, artist_font) + 8
    y += 6

    # Album — smaller gray.
    album = track.get("album", "")
    if album:
        album_font = get_font(22)
        for line in _wrap(draw, album, album_font, RIGHT_W, 2):
            draw.text((RIGHT_X, y), line, fill=LIGHT_GRAY, font=album_font)
            y += _text_h(draw, album_font) + 6

    # Footer.
    foot_font = get_font(16)
    updated = time.strftime("%-I:%M %p", time.localtime())
    footer = f"Last.fm · {username}    updated {updated}"
    draw.text((ART_MARGIN, HEIGHT - 26), footer, fill=LIGHT_GRAY, font=foot_font)

    img.save(output_path)
    logger.info("Saved now-playing card to %s", output_path)
    return output_path


def _render_centered(output_path, title, subtitle=None):
    """Render a simple centered message screen (config / unavailable states)."""
    img = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(img)

    title_font = get_font(40, bold=True)
    tw = draw.textbbox((0, 0), title, font=title_font)[2]
    ty = HEIGHT // 2 - (60 if subtitle else 20)
    draw.text(((WIDTH - tw) // 2, ty), title, fill=BLACK, font=title_font)

    if subtitle:
        sub_font = get_font(22)
        y = ty + _text_h(draw, title_font) + 24
        for line in subtitle:
            lw = draw.textbbox((0, 0), line, font=sub_font)[2]
            draw.text(((WIDTH - lw) // 2, y), line, fill=GRAY, font=sub_font)
            y += _text_h(draw, sub_font) + 10

    img.save(output_path)
    logger.info("Saved message screen (%s) to %s", title, output_path)
    return output_path


def _render_configure(output_path):
    return _render_centered(
        output_path,
        "Configure Last.fm",
        [
            "Add your free Last.fm API key and username to config.yml:",
            "",
            "now_playing.api_key   —  get one at last.fm/api",
            "now_playing.username  —  your Last.fm username",
        ],
    )


# ── Entry point ──────────────────────────────────────────────────────────────
def generate(config):
    """Generate the Now Playing display. Returns the output BMP path."""
    cfg = config.get("now_playing", {}) or {}
    output_path = cfg.get("output_path", "images/now_playing.bmp")
    api_key = (cfg.get("api_key") or "").strip()
    username = (cfg.get("username") or "").strip()
    cache_dir = cfg.get("cache_dir", "data/")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    os.makedirs(cache_dir, exist_ok=True)

    # No-config state (not an error).
    if not api_key or not username:
        logger.info("Last.fm api_key/username not configured — rendering setup screen")
        return _render_configure(output_path)

    track, art_available = _get_track(api_key, username, cache_dir)
    if track is None:
        logger.warning("No track data and no cache — rendering unavailable screen")
        return _render_centered(output_path, "Now Playing unavailable")

    _, art_path = _cache_paths(cache_dir)
    return _render_card(track, art_available, art_path, output_path, username)


if __name__ == "__main__":
    import yaml

    with open("config.yml") as f:
        cfg = yaml.safe_load(f)
    path = generate(cfg)
    print(f"Output: {path}")
