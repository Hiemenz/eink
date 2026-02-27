"""
Flight Radar module for E-Ink Display.

Displays live aircraft positions on a map with details about the 3 closest
planes. Emergency squawk 7700 flights are highlighted as priority.

Uses the OpenSky Network API (free tier) and CartoDB Positron grayscale tiles
for e-ink-friendly map rendering.

Layout (800x480):
  Left 600px  — map with aircraft silhouette icons (type-aware, heading-rotated)
  Right 200px — info panel with 3 closest aircraft details
"""

import json
import math
import os
import platform
import time

import requests
from datetime import datetime, timezone
from geopy.distance import geodesic
from PIL import Image, ImageDraw, ImageFont
from staticmap import StaticMap, CircleMarker

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EinkDisplay/1.0)"}

MAP_W, MAP_H = 600, 480
PANEL_W = 200
IMG_W, IMG_H = 800, 480

OPENSKY_STATES_URL = "https://opensky-network.org/api/states/all"
OPENSKY_FLIGHTS_URL = "https://opensky-network.org/api/flights/aircraft"
OPENSKY_TRACKS_URL = "https://opensky-network.org/api/tracks/all"

# Squawk code priority tiers
SQUAWK_EMERGENCY = {"7700"}          # general emergency
SQUAWK_HIGH      = {"7500", "7600"}  # hijack, radio failure


# ---------------------------------------------------------------------------
# Font helper
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
# Cache helpers (TTL-based, not daily)
# ---------------------------------------------------------------------------

def _cache_path(cache_dir):
    return os.path.join(cache_dir, "flight_cache.json")


def _load_cache(cache_dir, ttl_seconds):
    path = _cache_path(cache_dir)
    if not os.path.exists(path):
        return None
    age = time.time() - os.path.getmtime(path)
    if age > ttl_seconds:
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _load_stale_cache(cache_dir):
    """Load cache regardless of TTL (fallback when API is down)."""
    path = _cache_path(cache_dir)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _save_cache(cache_dir, data):
    os.makedirs(cache_dir, exist_ok=True)
    with open(_cache_path(cache_dir), "w") as f:
        json.dump(data, f)


# ---------------------------------------------------------------------------
# OpenSky API
# ---------------------------------------------------------------------------

def _fetch_aircraft(lat, lon, radius_deg, username="", password=""):
    """
    Fetch aircraft states from OpenSky bounding box query.
    Returns list of aircraft dicts, or None on failure.
    """
    params = {
        "lamin": lat - radius_deg,
        "lomin": lon - radius_deg,
        "lamax": lat + radius_deg,
        "lomax": lon + radius_deg,
    }
    auth = (username, password) if username and password else None
    try:
        resp = requests.get(OPENSKY_STATES_URL, params=params, auth=auth,
                            headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[flight] Failed to fetch aircraft states: {e}")
        return None

    states = data.get("states") or []
    aircraft = []
    for s in states:
        # Skip on-ground aircraft
        if s[8]:
            continue
        # Skip aircraft with no position
        if s[5] is None or s[6] is None:
            continue
        aircraft.append({
            "icao24":    s[0],
            "callsign":  (s[1] or "").strip(),
            "country":   s[2] or "",
            "longitude":  s[5],
            "latitude":   s[6],
            "altitude":   s[7],       # barometric altitude in meters
            "velocity":   s[9],       # ground speed in m/s
            "heading":    s[10],      # true track in degrees
            "squawk":     s[14] or "",
            "on_ground":  s[8],
            "category":  s[17] if len(s) > 17 else 0,
        })
    return aircraft


def _fetch_track(icao24, username="", password=""):
    """
    Fetch the live flight trajectory from OpenSky /tracks endpoint.
    Returns list of (lat, lon) tuples, or None on failure.
    """
    params = {"icao24": icao24, "time": 0}
    auth = (username, password) if username and password else None
    try:
        resp = requests.get(OPENSKY_TRACKS_URL, params=params, auth=auth,
                            headers=HEADERS, timeout=5)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[flight] Failed to fetch track for {icao24}: {e}")
        return None

    path = data.get("path")
    if not path:
        return None

    # Each waypoint: [time, lat, lon, baro_altitude, true_track, on_ground]
    return [(wp[1], wp[2]) for wp in path if wp[1] is not None and wp[2] is not None]


def _fetch_flight_details(icao24, username="", password=""):
    """
    Fetch origin/destination for an aircraft from OpenSky flights endpoint.
    Returns dict with estDepartureAirport and estArrivalAirport, or None.
    """
    now = int(time.time())
    params = {
        "icao24": icao24,
        "begin": now - 7200,  # last 2 hours
        "end": now,
    }
    auth = (username, password) if username and password else None
    try:
        resp = requests.get(OPENSKY_FLIGHTS_URL, params=params, auth=auth,
                            headers=HEADERS, timeout=10)
        resp.raise_for_status()
        flights = resp.json()
        if flights:
            f = flights[-1]  # most recent flight
            return {
                "origin": f.get("estDepartureAirport") or "?",
                "destination": f.get("estArrivalAirport") or "?",
                "first_seen": f.get("firstSeen"),
            }
    except Exception as e:
        print(f"[flight] Failed to fetch flight details for {icao24}: {e}")
    return None


# ---------------------------------------------------------------------------
# Aircraft selection
# ---------------------------------------------------------------------------

def _squawk_priority(squawk):
    """Return priority: 0=emergency, 1=high, 2=normal."""
    if squawk in SQUAWK_EMERGENCY:
        return 0
    if squawk in SQUAWK_HIGH:
        return 1
    return 2


def _select_display_aircraft(aircraft_list, center_lat, center_lon):
    """
    Select 3 aircraft for the info panel.
    Emergency squawk codes get priority, then closest by distance.
    """
    for ac in aircraft_list:
        ac["distance_nm"] = geodesic(
            (center_lat, center_lon),
            (ac["latitude"], ac["longitude"])
        ).nautical

    aircraft_list.sort(key=lambda a: (_squawk_priority(a["squawk"]), a["distance_nm"]))
    return aircraft_list[:3]


# ---------------------------------------------------------------------------
# Map rendering
# ---------------------------------------------------------------------------

def _draw_flight_trail(draw, track, center_lat, center_lon, zoom, is_emergency):
    """Draw a dotted trail of small circles for a flight's trajectory."""
    color = (220, 150, 150) if is_emergency else (160, 160, 160)
    for lat, lon in track:
        px, py = _latlon_to_pixel(lat, lon, center_lat, center_lon, zoom, MAP_W, MAP_H)
        if 0 <= px < MAP_W and 0 <= py < MAP_H:
            draw.ellipse([px - 2, py - 2, px + 2, py + 2], fill=color)


def _render_map(aircraft_list, center_lat, center_lon, zoom, emergencies,
                display_tracks=None):
    """Render the map portion using staticmap with CartoDB Positron tiles."""
    display_tracks = display_tracks or {}
    m = StaticMap(MAP_W, MAP_H,
                  url_template="https://cartodb-basemaps-a.global.ssl.fastly.net/light_all/{z}/{x}/{y}.png")

    # Add a transparent center marker so staticmap centers correctly
    m.add_marker(CircleMarker((center_lon, center_lat), (180, 180, 180), 4))

    try:
        img = m.render(zoom=zoom, center=(center_lon, center_lat))
    except Exception as e:
        print(f"[flight] Map tile fetch failed: {e}")
        img = Image.new("RGB", (MAP_W, MAP_H), (240, 240, 240))

    draw = ImageDraw.Draw(img)

    # Draw flight trails BEFORE aircraft icons so icons render on top
    for icao24, track in display_tracks.items():
        is_emergency = icao24 in emergencies
        _draw_flight_trail(draw, track, center_lat, center_lon, zoom, is_emergency)

    # Plot aircraft icons
    for ac in aircraft_list:
        px, py = _latlon_to_pixel(ac["latitude"], ac["longitude"],
                                   center_lat, center_lon, zoom, MAP_W, MAP_H)
        if 0 <= px < MAP_W and 0 <= py < MAP_H:
            is_emergency = ac["squawk"] in SQUAWK_EMERGENCY
            is_high = ac["squawk"] in SQUAWK_HIGH
            _draw_aircraft_icon(draw, px, py, ac.get("heading", 0),
                                ac.get("category", 0),
                                is_emergency, is_high)

    # Status bar at bottom of map
    count = len(aircraft_list)
    now_str = datetime.now().strftime("%H:%M")
    status = f"[{count} aircraft]  [{now_str}]"
    sf = _font(13)
    sb = draw.textbbox((0, 0), status, font=sf)
    sw = sb[2] - sb[0]
    # Semi-transparent background
    draw.rectangle([5, MAP_H - 22, sw + 15, MAP_H - 2], fill=(255, 255, 255))
    draw.text((10, MAP_H - 20), status, fill=(80, 80, 80), font=sf)

    return img


def _latlon_to_pixel(lat, lon, center_lat, center_lon, zoom, w, h):
    """Convert lat/lon to pixel coordinates on the rendered map tile."""
    n = 2.0 ** zoom
    # Center pixel
    cx_tile = (center_lon + 180.0) / 360.0 * n
    cy_tile = (1.0 - math.log(math.tan(math.radians(center_lat)) +
               1.0 / math.cos(math.radians(center_lat))) / math.pi) / 2.0 * n
    # Target pixel
    tx_tile = (lon + 180.0) / 360.0 * n
    ty_tile = (1.0 - math.log(math.tan(math.radians(lat)) +
               1.0 / math.cos(math.radians(lat))) / math.pi) / 2.0 * n

    px = int((tx_tile - cx_tile) * 256 + w / 2)
    py = int((ty_tile - cy_tile) * 256 + h / 2)
    return px, py


def _silhouette_small_plane():
    """Small prop plane: straight wings, narrow fuselage, single tail fin."""
    return [
        (0, -8),              # nose
        (-1, -4),
        (-8, -1),             # left wing tip
        (-8, 1),
        (-1, 0),
        (-1, 5),
        (-4, 7),              # left stabilizer
        (-4, 8),
        (0, 7),
        (4, 8),               # right stabilizer
        (4, 7),
        (1, 5),
        (1, 0),
        (8, 1),               # right wing tip
        (8, -1),
        (1, -4),
    ]


def _silhouette_private_jet():
    """Private jet: swept wings, T-tail, slim body."""
    return [
        (0, -9),              # nose
        (-1, -5),
        (-7, 0),              # left wing tip (swept)
        (-6, 2),
        (-1, 0),
        (-1, 5),
        (-3, 7),              # left T-tail
        (-3, 8),
        (0, 7),
        (3, 8),               # right T-tail
        (3, 7),
        (1, 5),
        (1, 0),
        (6, 2),               # right wing tip (swept)
        (7, 0),
        (1, -5),
    ]


def _silhouette_airliner():
    """Airliner: wide swept wings, engine bumps, horizontal stabilizer."""
    return [
        (0, -10),             # nose
        (-2, -5),
        (-10, -1),            # left wing tip
        (-10, 1),
        (-5, 0),              # left engine pod area
        (-2, 1),
        (-2, 5),
        (-5, 7),              # left stabilizer
        (-5, 8),
        (0, 7),
        (5, 8),               # right stabilizer
        (5, 7),
        (2, 5),
        (2, 1),
        (5, 0),               # right engine pod area
        (10, 1),              # right wing tip
        (10, -1),
        (2, -5),
    ]


def _silhouette_military():
    """Military jet: delta/swept wings, single vertical stabilizer."""
    return [
        (0, -10),             # nose
        (-2, -4),
        (-9, 4),              # left delta wing tip
        (-8, 6),
        (-2, 3),
        (-3, 7),              # left tail
        (-2, 8),
        (0, 7),
        (2, 8),               # right tail
        (3, 7),
        (2, 3),
        (8, 6),               # right delta wing tip
        (9, 4),
        (2, -4),
    ]


def _silhouette_helicopter():
    """Helicopter: rotor disc (approximated as octagon), stubby body, tail boom."""
    # Rotor disc as octagon centered at body
    r = 7
    rotor = []
    for i in range(8):
        a = math.pi * 2 * i / 8
        rotor.append((r * math.sin(a), -2 + r * -math.cos(a)))
    # Body + tail boom drawn separately
    body = [
        (-2, -2), (2, -2),   # body top
        (2, 3), (1, 4),      # body bottom
        (1, 9),               # tail boom end
        (0, 10),
        (-1, 9),
        (-1, 4), (-2, 3),    # body bottom left
    ]
    return rotor, body


def _pick_silhouette(category):
    """Pick silhouette points based on OpenSky category value."""
    cat = category if isinstance(category, int) else 0
    if cat == 2:
        return _silhouette_small_plane(), False
    elif cat == 3:
        return _silhouette_private_jet(), False
    elif cat in (4, 5, 6):
        return _silhouette_airliner(), False
    elif cat == 7:
        return _silhouette_military(), False
    elif cat == 8:
        return _silhouette_helicopter(), True
    else:
        return _silhouette_small_plane(), False


def _draw_aircraft_icon(draw, x, y, heading, category, is_emergency, is_high):
    """Draw a heading-rotated aircraft silhouette at (x, y)."""
    angle = math.radians(heading or 0)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)

    if is_emergency:
        fill = (220, 30, 30)
        outline = (255, 200, 0)
    elif is_high:
        fill = (230, 140, 0)
        outline = (180, 100, 0)
    else:
        fill = (30, 30, 30)
        outline = (60, 60, 60)

    silhouette, is_heli = _pick_silhouette(category)

    def _rotate_and_translate(points):
        return [(x + px * cos_a - py * sin_a,
                 y + px * sin_a + py * cos_a)
                for px, py in points]

    if is_heli:
        rotor_pts, body_pts = silhouette
        draw.polygon(_rotate_and_translate(body_pts), fill=fill, outline=outline)
        draw.polygon(_rotate_and_translate(rotor_pts), fill=None, outline=outline)
    else:
        draw.polygon(_rotate_and_translate(silhouette), fill=fill, outline=outline)


# ---------------------------------------------------------------------------
# Info panel rendering
# ---------------------------------------------------------------------------

def _render_info_panel(draw, display_aircraft, panel_x, width, height,
                       username="", password=""):
    """Render the right info panel with up to 3 aircraft sections."""
    section_h = height // 3
    small = _font(12)
    medium = _font(15)
    title_font = _font(17)

    for i, ac in enumerate(display_aircraft):
        y_start = i * section_h
        y = y_start + 8

        squawk = ac.get("squawk", "")
        is_emergency = squawk in SQUAWK_EMERGENCY
        is_high = squawk in SQUAWK_HIGH

        # Section background for emergencies
        if is_emergency:
            draw.rectangle([panel_x, y_start, panel_x + width, y_start + section_h],
                           fill=(255, 220, 220))
        elif is_high:
            draw.rectangle([panel_x, y_start, panel_x + width, y_start + section_h],
                           fill=(255, 240, 210))

        # Callsign
        callsign = ac.get("callsign", "N/A") or "N/A"
        prefix = "!! " if is_emergency else ""
        label = f"{prefix}{callsign}"
        draw.text((panel_x + 8, y), label,
                  fill=(200, 0, 0) if is_emergency else (20, 20, 20),
                  font=title_font)
        y += 22

        # Altitude and speed
        alt_m = ac.get("altitude")
        alt_ft = int(alt_m * 3.281) if alt_m else 0
        vel_ms = ac.get("velocity")
        vel_kts = int(vel_ms * 1.944) if vel_ms else 0
        draw.text((panel_x + 8, y), f"{alt_ft:,} ft  {vel_kts} kts",
                  fill=(60, 60, 60), font=small)
        y += 16

        # Origin → Destination (fetch details)
        details = _fetch_flight_details(ac["icao24"], username, password)
        if details:
            origin = details.get("origin", "?")
            dest = details.get("destination", "?")
            draw.text((panel_x + 8, y), f"{origin} → {dest}",
                      fill=(60, 60, 60), font=medium)
            y += 20

            # Time airborne
            first_seen = details.get("first_seen")
            if first_seen:
                elapsed = int(time.time()) - first_seen
                hours = elapsed // 3600
                mins = (elapsed % 3600) // 60
                draw.text((panel_x + 8, y), f"{hours}h {mins:02d}m",
                          fill=(100, 100, 100), font=small)
                y += 16
        else:
            draw.text((panel_x + 8, y), "Route: N/A",
                      fill=(120, 120, 120), font=small)
            y += 18

        # Distance
        dist = ac.get("distance_nm", 0)
        draw.text((panel_x + 8, y), f"{dist:.0f} nm away",
                  fill=(100, 100, 100), font=small)

        # Section divider
        if i < 2:
            div_y = y_start + section_h - 1
            draw.line([(panel_x + 5, div_y), (panel_x + width - 5, div_y)],
                      fill=(180, 180, 180), width=1)


# ---------------------------------------------------------------------------
# Fallback image
# ---------------------------------------------------------------------------

def _render_fallback(output_path):
    img = Image.new("RGB", (IMG_W, IMG_H), "white")
    draw = ImageDraw.Draw(img)
    hf = _font(28)
    sf = _font(18)
    draw.text((200, 200), "Flight Radar", fill=(30, 30, 30), font=hf)
    draw.text((200, 240), "Data unavailable.", fill=(120, 120, 120), font=sf)
    draw.text((200, 266), "Check your connection.", fill=(120, 120, 120), font=sf)
    img.save(output_path)
    print(f"[flight] Fallback image saved to {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def generate(config):
    """Generate Flight Radar image. Return output path."""
    cfg = config.get("flight_radar", {})
    output_path = cfg.get("output_path", "flight_display.bmp")
    cache_dir = cfg.get("cache_dir", "data/")
    radius_deg = cfg.get("radius_deg", 1.0)
    cache_ttl = cfg.get("cache_ttl_seconds", 300)
    username = cfg.get("opensky_username", "")
    password = cfg.get("opensky_password", "")
    zoom = cfg.get("map_zoom", 9)

    loc = config.get("forecast_location", {})
    center_lat = loc.get("latitude", 35.8911)
    center_lon = loc.get("longitude", -86.8217)

    # Try cache first
    cached = _load_cache(cache_dir, cache_ttl)
    if cached:
        all_aircraft = cached
        print(f"[flight] Using cached data ({len(all_aircraft)} aircraft)")
    else:
        all_aircraft = _fetch_aircraft(center_lat, center_lon, radius_deg,
                                       username, password)
        if all_aircraft is None:
            # Try stale cache as fallback
            all_aircraft = _load_stale_cache(cache_dir)
            if all_aircraft is None:
                return _render_fallback(output_path)
            print(f"[flight] Using stale cache ({len(all_aircraft)} aircraft)")
        else:
            _save_cache(cache_dir, all_aircraft)
            print(f"[flight] Fetched {len(all_aircraft)} aircraft")

    if not all_aircraft:
        return _render_fallback(output_path)

    # Select 3 display aircraft (emergency priority + closest)
    display = _select_display_aircraft(list(all_aircraft), center_lat, center_lon)
    emergencies = {ac["icao24"] for ac in display
                   if ac.get("squawk") in SQUAWK_EMERGENCY | SQUAWK_HIGH}

    # Fetch flight tracks for display aircraft only (avoids rate limiting)
    display_tracks = {}
    for ac in display:
        track = _fetch_track(ac["icao24"], username, password)
        if track:
            display_tracks[ac["icao24"]] = track

    # Render map
    map_img = _render_map(all_aircraft, center_lat, center_lon, zoom, emergencies,
                          display_tracks)

    # Composite final image
    img = Image.new("RGB", (IMG_W, IMG_H), "white")
    img.paste(map_img, (0, 0))

    # Vertical divider
    draw = ImageDraw.Draw(img)
    draw.line([(MAP_W, 0), (MAP_W, IMG_H)], fill=(180, 180, 180), width=1)

    # Info panel
    _render_info_panel(draw, display, MAP_W, PANEL_W, IMG_H, username, password)

    img.save(output_path)
    print(f"[flight] Saved to {output_path}")
    return output_path


if __name__ == "__main__":
    import yaml
    with open("config.yml") as f:
        cfg = yaml.safe_load(f)
    path = generate(cfg)
    print(f"Output: {path}")
