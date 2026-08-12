"""
SiriusXM Now Playing module.

Shows the song currently playing on a SiriusXM channel (default: "Life with
John Mayer") with its cover art, plus a QR code linking to the track on
Spotify.

SiriusXM has no public "now playing" API. This talks to the same
undocumented web-player endpoints the SiriusXM site itself uses
(player.siriusxm.com/rest/v2 and v4), the same approach used by the
long-running open-source `sxm-client` project. It requires your own
SiriusXM login (a real subscription) and is not officially supported by
SiriusXM — the endpoints can change or break without notice, and logging
in from here may end another active SiriusXM session on your account
(SiriusXM limits concurrent streams per subscription).

Cover art and the canonical track URL come from the Spotify Web API
(client-credentials flow — no user login, just a free app registration).
If SiriusXM's own album art is present it's used as a fallback when
Spotify has no match; the QR code is only drawn when a Spotify match
was found.

Config section (add to config.yml):
  siriusxm_now_playing:
    output_path: images/siriusxm_now_playing.bmp
    cache_dir: data/
    username: ""                      # your SiriusXM account email/username
    password: ""                      # your SiriusXM account password
    channel_name: "Life with John Mayer"   # name, id, or channel number
    spotify_client_id: ""             # free app at developer.spotify.com
    spotify_client_secret: ""

States:
  * No SiriusXM username/password -> "Configure SiriusXM" screen.
  * Fetch fails, no cache          -> "SiriusXM unavailable" centered.
  * Fetch succeeds, no song cut (talk segment) and no cache -> "no song
    currently playing" centered.
  * No Spotify credentials / no match -> card renders without the QR code,
    using SiriusXM's own album art if available.
"""

import base64
import datetime
import json
import os
import pickle
import sys
import time
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import qrcode
import qrcode.constants
import requests
from PIL import Image, ImageDraw

from utils import get_font, get_logger

logger = get_logger("siriusxm_now_playing")

WIDTH, HEIGHT = 800, 480

WHITE = (255, 255, 255)
BLACK = (20, 20, 20)
DARK = (30, 30, 30)
GRAY = (110, 110, 110)
LIGHT_GRAY = (170, 170, 170)
BORDER = (60, 60, 60)

ART_SIZE = 220
QR_SIZE = 190
GAP = 40
ROW_Y = 20

REST_V2_FORMAT = "https://player.siriusxm.com/rest/v2/experience/modules/{}"
REST_V4_FORMAT = "https://player.siriusxm.com/rest/v4/experience/modules/{}"
SXM_APP_VERSION = "5.36.514"
SXM_DEVICE_MODEL = "EverestWebClient"
SXM_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EinkDisplay/1.0)"}

SESSION_CACHE_FILE = "siriusxm_session.pkl"
CHANNEL_CACHE_FILE = "siriusxm_channel_cache.json"
CHANNEL_CACHE_TTL = 7 * 86400
NOW_PLAYING_CACHE_FILE = "siriusxm_now_playing_cache.json"
ART_CACHE_FILE = "siriusxm_now_playing_art.png"
SPOTIFY_TOKEN_CACHE_FILE = "spotify_token_cache.json"

SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_SEARCH_URL = "https://api.spotify.com/v1/search"


# ── SiriusXM session / auth ─────────────────────────────────────────────────
def _device_info():
    return {
        "resultTemplate": "web",
        "deviceInfo": {
            "osVersion": "Windows",
            "platform": "Web",
            "sxmAppVersion": SXM_APP_VERSION,
            "browser": "Chrome",
            "browserVersion": "124.0.0",
            "appRegion": "US",
            "deviceModel": SXM_DEVICE_MODEL,
            "clientDeviceId": "null",
            "player": "html5",
            "clientDeviceType": "web",
        },
    }


def _load_session(cache_dir):
    session = requests.Session()
    session.headers.update({"User-Agent": SXM_USER_AGENT, "Accept": "application/json"})
    path = os.path.join(cache_dir, SESSION_CACHE_FILE)
    try:
        with open(path, "rb") as f:
            session.cookies.update(pickle.load(f))
    except Exception:
        pass
    return session


def _save_session(session, cache_dir):
    if session is None:
        return
    path = os.path.join(cache_dir, SESSION_CACHE_FILE)
    try:
        with open(path, "wb") as f:
            pickle.dump(session.cookies, f)
    except Exception as e:
        logger.warning("Could not persist SiriusXM session: %s", e)


def _sxm_post(session, path, postdata, url_format=REST_V2_FORMAT):
    body = {"moduleList": {"modules": [{"moduleRequest": postdata}]}}
    try:
        resp = session.post(url_format.format(path), json=body, timeout=15)
        resp.raise_for_status()
        return resp.json()["ModuleListResponse"]
    except Exception as e:
        logger.warning("SXM POST %s failed: %s", path, e)
        return None


def _sxm_get(session, path, params, url_format=REST_V2_FORMAT):
    try:
        resp = session.get(url_format.format(path), params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()["ModuleListResponse"]
    except Exception as e:
        logger.warning("SXM GET %s failed: %s", path, e)
        return None


def _login(session, username, password):
    postdata = _device_info()
    postdata["standardAuth"] = {"username": username, "password": password}
    data = _sxm_post(session, "modify/authentication", postdata)
    if not data:
        return False
    ok = data.get("status") == 1 and "SXMAUTHNEW" in session.cookies
    if not ok:
        logger.warning("SiriusXM login failed — check username/password")
    return ok


def _authenticate(session):
    data = _sxm_post(session, "resume?OAtrial=false", _device_info())
    if not data:
        return False
    return (
        data.get("status") == 1
        and "AWSALB" in session.cookies
        and "JSESSIONID" in session.cookies
    )


def _ensure_session(session, username, password):
    if "AWSALB" in session.cookies and "JSESSIONID" in session.cookies:
        return True
    if "SXMAUTHNEW" not in session.cookies and not _login(session, username, password):
        return False
    return _authenticate(session)


# ── Channel lookup ───────────────────────────────────────────────────────────
def _get_channels(session):
    body = {
        "moduleList": {
            "modules": [
                {
                    "moduleArea": "Discovery",
                    "moduleType": "ChannelListing",
                    "moduleRequest": {"resultTemplate": "responsive"},
                }
            ]
        }
    }
    try:
        resp = session.post(REST_V4_FORMAT.format("get?type=2"), json=body, timeout=15)
        resp.raise_for_status()
        data = resp.json()["ModuleListResponse"]
        return data["moduleList"]["modules"][0]["moduleResponse"]["contentData"][
            "channelListing"
        ]["channels"]
    except Exception as e:
        logger.warning("SXM channel list fetch failed: %s", e)
        return []


def _find_channel(channels, query):
    q = query.strip().lower()
    for ch in channels:
        if ch.get("name", "").strip().lower() == q:
            return ch
    for ch in channels:
        if q in ch.get("name", "").strip().lower():
            return ch
    for ch in channels:
        if str(ch.get("siriusChannelNumber")) == q:
            return ch
    return None


def _load_cached_channel(session, channel_query, cache_dir):
    path = os.path.join(cache_dir, CHANNEL_CACHE_FILE)
    cached = _read_json(path)
    if (
        cached
        and cached.get("query") == channel_query
        and time.time() - cached.get("cached_at", 0) < CHANNEL_CACHE_TTL
    ):
        return cached["channel"]

    channels = _get_channels(session)
    channel = _find_channel(channels, channel_query)
    if channel is None:
        logger.warning("SiriusXM channel '%s' not found", channel_query)
        return None

    _write_json(path, {"query": channel_query, "cached_at": time.time(), "channel": channel})
    return channel


# ── Now playing ──────────────────────────────────────────────────────────────
def _now_playing_raw(session, channel):
    now = time.time()
    now_dt = datetime.datetime.fromtimestamp(now).replace(tzinfo=datetime.timezone.utc)
    params = {
        "assetGUID": channel["channelGuid"],
        "ccRequestType": "AUDIO_VIDEO",
        "channelId": channel["channelId"],
        "hls_output_mode": "custom",
        "marker_mode": "all_separate_cue_points",
        "result-template": "web",
        "time": str(int(round(now * 1000.0))),
        "timestamp": now_dt.isoformat("T") + "Z",
    }
    return _sxm_get(session, "tune/now-playing-live", params)


def _fetch_now_playing(session, channel, username, password):
    data = _now_playing_raw(session, channel)
    code = None
    if data:
        try:
            code = data["messages"][0]["code"]
        except (KeyError, IndexError):
            code = None

    if data is None or code in (201, 204, 208):
        if code == 204:
            session.cookies.clear()
        if _login(session, username, password) and _authenticate(session):
            data = _now_playing_raw(session, channel)
    return data


def _extract_latest_song(data):
    try:
        marker_lists = data["moduleList"]["modules"][0]["moduleResponse"]["liveChannelData"][
            "markerLists"
        ]
    except (KeyError, IndexError, TypeError):
        return None

    cut_markers = []
    for ml in marker_lists:
        if ml.get("layer") == "cut":
            cut_markers.extend(ml.get("markers", []))

    now_ms = time.time() * 1000
    song_markers = [
        m
        for m in cut_markers
        if "cut" in m
        and m["cut"].get("cutContentType") == "Song"
        and m.get("time", 0) <= now_ms
    ]
    if not song_markers:
        return None

    latest = max(song_markers, key=lambda m: m["time"])
    cut = latest["cut"]

    artist = ", ".join(a.get("name", "") for a in cut.get("artists", []) if a.get("name"))
    album = cut.get("album") or {}
    art_url = None
    for art in album.get("creativeArts", []):
        if art.get("type") == "IMAGE" and art.get("url"):
            art_url = art["url"]
            break

    return {
        "title": (cut.get("title") or "").strip(),
        "artist": artist,
        "album": album.get("title") or "",
        "art_url": art_url,
    }


def _get_song(username, password, channel_query, cache_dir):
    session = _load_session(cache_dir)

    if not _ensure_session(session, username, password):
        return None, session

    channel = _load_cached_channel(session, channel_query, cache_dir)
    if channel is None:
        return None, session

    data = _fetch_now_playing(session, channel, username, password)
    if data is None:
        return None, session

    return _extract_latest_song(data), session


# ── Spotify ──────────────────────────────────────────────────────────────────
def _spotify_token(client_id, client_secret, cache_dir):
    path = os.path.join(cache_dir, SPOTIFY_TOKEN_CACHE_FILE)
    cached = _read_json(path)
    if cached and time.time() < cached.get("expires_at", 0):
        return cached["access_token"]

    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    try:
        resp = requests.post(
            SPOTIFY_TOKEN_URL,
            headers={"Authorization": f"Basic {auth}"},
            data={"grant_type": "client_credentials"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("Spotify token fetch failed: %s", e)
        return None

    token = data.get("access_token")
    if token:
        _write_json(
            path,
            {"access_token": token, "expires_at": time.time() + data.get("expires_in", 3600) - 60},
        )
    return token


def _spotify_lookup(token, title, artist):
    query = f"track:{title} artist:{artist}" if artist else title
    try:
        resp = requests.get(
            SPOTIFY_SEARCH_URL,
            headers={"Authorization": f"Bearer {token}"},
            params={"q": query, "type": "track", "limit": 1},
            timeout=15,
        )
        resp.raise_for_status()
        items = resp.json().get("tracks", {}).get("items", [])
    except Exception as e:
        logger.warning("Spotify search failed: %s", e)
        return None

    if not items:
        return None

    track = items[0]
    images = (track.get("album") or {}).get("images", [])
    return {
        "url": (track.get("external_urls") or {}).get("spotify"),
        "art_url": images[0]["url"] if images else None,
    }


# ── Small JSON / art helpers ─────────────────────────────────────────────────
def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _write_json(path, data):
    try:
        with open(path, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.warning("Could not write cache %s: %s", path, e)


def _download_art(url, art_path):
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


# ── Rendering ────────────────────────────────────────────────────────────────
def _wrap(draw, text, font, max_width, max_lines):
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

    if len(lines) == max_lines and (len(" ".join(lines).split()) < len(words)):
        last = lines[-1]
        while last and draw.textbbox((0, 0), last + "…", font=font)[2] > max_width:
            last = last[:-1].rstrip()
        lines[-1] = last + "…"
    return lines


def _text_h(draw, font):
    return draw.textbbox((0, 0), "Ag", font=font)[3]


def _fit_title(draw, text, max_width, max_lines):
    for size in (40, 36, 32, 28, 24):
        font = get_font(size, bold=True)
        lines = _wrap(draw, text, font, max_width, max_lines)
        line_h = _text_h(draw, font) + 6
        if len(lines) <= max_lines and lines and not lines[-1].endswith("…"):
            return font, lines, line_h
        if size == 24:
            return font, lines, line_h
    font = get_font(24, bold=True)
    return font, _wrap(draw, text, font, max_width, max_lines), _text_h(draw, font) + 6


def _draw_centered_line(draw, text, y, font, fill):
    w = draw.textbbox((0, 0), text, font=font)[2]
    draw.text(((WIDTH - w) // 2, y), text, fill=fill, font=font)


def _draw_placeholder_art(draw, x, y, size):
    draw.rectangle([x, y, x + size, y + size], fill=(238, 238, 238), outline=BORDER, width=2)
    cx, cy = x + size // 2, y + size // 2
    r = size // 6
    draw.ellipse([cx - r - 10, cy + r, cx - 10, cy + 2 * r], fill=GRAY)
    draw.ellipse([cx + 30, cy + r - 6, cx + r + 40, cy + 2 * r - 6], fill=GRAY)
    stem_top = cy - r
    draw.rectangle([cx - 12, stem_top, cx - 8, cy + r + r // 2], fill=GRAY)
    draw.rectangle([cx + r + 38, stem_top - 6, cx + r + 42, cy + r + r // 2 - 6], fill=GRAY)
    draw.rectangle([cx - 12, stem_top, cx + r + 42, stem_top + 12], fill=GRAY)


def _paste_art(canvas, art_path, x, y, size):
    try:
        art = Image.open(art_path).convert("RGB")
    except Exception as e:
        logger.warning("Could not open cached art: %s", e)
        return False
    side = min(art.width, art.height)
    left = (art.width - side) // 2
    top = (art.height - side) // 2
    art = art.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)
    canvas.paste(art, (x, y))
    ImageDraw.Draw(canvas).rectangle([x, y, x + size, y + size], outline=BORDER, width=2)
    return True


def _make_qr_image(url, size):
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    return img.resize((size, size), Image.NEAREST)


def _render_card(song, art_path, art_ready, channel_name, output_path):
    img = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(img)

    has_qr = bool(song.get("spotify_url"))
    block_w = ART_SIZE + (GAP + QR_SIZE if has_qr else 0)
    art_x = (WIDTH - block_w) // 2
    art_y = ROW_Y

    pasted = art_ready and _paste_art(img, art_path, art_x, art_y, ART_SIZE)
    if not pasted:
        _draw_placeholder_art(draw, art_x, art_y, ART_SIZE)

    if has_qr:
        qr_x = art_x + ART_SIZE + GAP
        qr_y = ROW_Y + (ART_SIZE - QR_SIZE) // 2
        qr_img = _make_qr_image(song["spotify_url"], QR_SIZE)
        img.paste(qr_img, (qr_x, qr_y))
        draw.rectangle(
            [qr_x - 2, qr_y - 2, qr_x + QR_SIZE + 2, qr_y + QR_SIZE + 2], outline=BORDER, width=1
        )
        cap_font = get_font(14)
        cap = "Scan to open in Spotify"
        cw = draw.textbbox((0, 0), cap, font=cap_font)[2]
        draw.text((qr_x + (QR_SIZE - cw) // 2, qr_y + QR_SIZE + 8), cap, fill=GRAY, font=cap_font)

    text_y = ROW_Y + ART_SIZE + 34
    text_max_w = WIDTH - 80

    badge_font = get_font(16, bold=True)
    badge = f"NOW PLAYING · {channel_name.upper()}"
    _draw_centered_line(draw, badge, text_y, badge_font, GRAY)
    text_y += _text_h(draw, badge_font) + 14

    title_font, title_lines, title_lh = _fit_title(draw, song.get("title", ""), text_max_w, 2)
    for line in title_lines:
        _draw_centered_line(draw, line, text_y, title_font, BLACK)
        text_y += title_lh
    text_y += 6

    artist_font = get_font(24)
    if song.get("artist"):
        for line in _wrap(draw, song["artist"], artist_font, text_max_w, 1):
            _draw_centered_line(draw, line, text_y, artist_font, GRAY)
            text_y += _text_h(draw, artist_font) + 6

    if song.get("album"):
        album_font = get_font(18)
        for line in _wrap(draw, song["album"], album_font, text_max_w, 1):
            _draw_centered_line(draw, line, text_y, album_font, LIGHT_GRAY)

    foot_font = get_font(16)
    updated = time.strftime("%-I:%M %p", time.localtime())
    footer = f"SiriusXM · {channel_name}    updated {updated}"
    draw.text((30, HEIGHT - 26), footer, fill=LIGHT_GRAY, font=foot_font)

    img.save(output_path)
    logger.info("Saved SiriusXM now-playing card to %s", output_path)
    return output_path


def _render_centered(output_path, title, subtitle=None):
    img = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(img)

    title_font = get_font(40, bold=True)
    ty = HEIGHT // 2 - (60 if subtitle else 20)
    _draw_centered_line(draw, title, ty, title_font, BLACK)

    if subtitle:
        sub_font = get_font(22)
        y = ty + _text_h(draw, title_font) + 24
        max_w = WIDTH - 80
        for raw_line in subtitle:
            if not raw_line:
                y += _text_h(draw, sub_font) + 10
                continue
            for line in _wrap(draw, raw_line, sub_font, max_w, 99):
                _draw_centered_line(draw, line, y, sub_font, GRAY)
                y += _text_h(draw, sub_font) + 10

    img.save(output_path)
    logger.info("Saved message screen (%s) to %s", title, output_path)
    return output_path


def _render_configure(output_path):
    return _render_centered(
        output_path,
        "Configure SiriusXM",
        [
            "Add your SiriusXM login to config.yml under",
            "siriusxm_now_playing: username / password",
            "",
            "Add a Spotify app under spotify_client_id /",
            "spotify_client_secret (free at developer.spotify.com)",
        ],
    )


# ── Entry point ──────────────────────────────────────────────────────────────
def generate(config):
    """Generate the SiriusXM Now Playing display. Returns the output BMP path."""
    cfg = config.get("siriusxm_now_playing", {}) or {}
    output_path = cfg.get("output_path", "images/siriusxm_now_playing.bmp")
    cache_dir = cfg.get("cache_dir", "data/")
    username = (cfg.get("username") or "").strip()
    password = (cfg.get("password") or "").strip()
    channel_query = (cfg.get("channel_name") or "Life with John Mayer").strip()
    spotify_client_id = (cfg.get("spotify_client_id") or "").strip()
    spotify_client_secret = (cfg.get("spotify_client_secret") or "").strip()

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    os.makedirs(cache_dir, exist_ok=True)

    if not username or not password:
        logger.info("SiriusXM username/password not configured — rendering setup screen")
        return _render_configure(output_path)

    song, session = _get_song(username, password, channel_query, cache_dir)
    _save_session(session, cache_dir)

    cache_path = os.path.join(cache_dir, NOW_PLAYING_CACHE_FILE)
    art_path = os.path.join(cache_dir, ART_CACHE_FILE)
    cached = _read_json(cache_path)

    if song is None or not song.get("title"):
        if cached:
            logger.info("No current song from SiriusXM — reusing last cached song")
            return _render_card(cached, art_path, os.path.exists(art_path), channel_query, output_path)
        return _render_centered(
            output_path, "No song playing", [f"Nothing musical on {channel_query} right now."]
        )

    same_song = (
        cached
        and cached.get("title") == song["title"]
        and cached.get("artist") == song["artist"]
    )

    if same_song:
        song["spotify_url"] = cached.get("spotify_url")
        art_ready = os.path.exists(art_path)
    else:
        song["spotify_url"] = None
        spotify_art_url = None

        if spotify_client_id and spotify_client_secret:
            token = _spotify_token(spotify_client_id, spotify_client_secret, cache_dir)
            if token:
                sp = _spotify_lookup(token, song["title"], song["artist"])
                if sp:
                    song["spotify_url"] = sp.get("url")
                    spotify_art_url = sp.get("art_url")

        # Prefer SiriusXM's own art for what's actually airing — it's the
        # authoritative cover for this specific cut (e.g. a live version).
        # Spotify's art is only a fallback when SXM sent none.
        art_source = song.get("art_url") or spotify_art_url
        art_ready = _download_art(art_source, art_path)

    _write_json(cache_path, song)

    return _render_card(song, art_path, art_ready, channel_query, output_path)


if __name__ == "__main__":
    import yaml

    with open("config.yml") as f:
        cfg = yaml.safe_load(f)
    path = generate(cfg)
    print(f"Output: {path}")
