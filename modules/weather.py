import time
import json
import requests
import io
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import qrcode
from modules.special_weather import get_special_weather_messages, get_alert_headline
from eink_generator import load_config
from modules.forecast import get_detailed_forecast, generate_forecast_image
from modules.moon_phase import _moon_age, _phase_fraction, _phase_name, _illumination
from datetime import datetime as _dt, date as _date, timedelta as _td
from zoneinfo import ZoneInfo
from astral import LocationInfo
from astral.sun import sun as _astral_sun
import math
import platform
import os

from utils import get_font, get_logger

logger = get_logger("weather")

NWS_KM_PER_PX = 1.533  # km per pixel in original NWS 600×550 radar image

# ---------------------------------------------------------------------------
# Current-conditions cache (5-minute TTL to avoid repeated API calls)
# ---------------------------------------------------------------------------
_conditions_cache = {"data": None, "ts": 0}
_CONDITIONS_TTL = 300  # seconds

# Last-known-good conditions (survives TTL expiry; used as stale fallback)
_last_good_conditions: Dict[str, Any] = {"data": None, "ts": 0}

_RETRY_WAIT = 60  # seconds to wait before a retry on failure


def _deg_to_compass(deg: float) -> str:
    """Convert wind direction degrees (0-360) to 16-point compass string."""
    directions = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return directions[round(deg / 22.5) % 16]


def _wmo_description(code: int) -> str:
    """WMO weather code → short human label."""
    table = {
        0: "Clear", 1: "Mainly Clear", 2: "Partly Cloudy", 3: "Overcast",
        45: "Fog", 48: "Icy Fog",
        51: "Light Drizzle", 53: "Drizzle", 55: "Heavy Drizzle",
        61: "Light Rain", 63: "Rain", 65: "Heavy Rain",
        71: "Light Snow", 73: "Snow", 75: "Heavy Snow",
        77: "Snow Grains",
        80: "Showers", 81: "Showers", 82: "Heavy Showers",
        85: "Snow Showers", 86: "Heavy Snow Showers",
        95: "Thunderstorm", 96: "Severe T-Storm", 99: "Severe T-Storm",
    }
    return table.get(code, f"Code {code}")


def _parse_time(iso_str):
    """Parse ISO 8601 datetime string to '6:42 AM' display format."""
    try:
        dt = _dt.fromisoformat(iso_str)
        return dt.strftime("%-I:%M %p")
    except Exception:
        return iso_str


def _do_fetch_conditions(url: str, lat: float, lon: float, headers: dict) -> Optional[Dict[str, Any]]:
    """Single HTTP attempt: fetch *url*, parse the JSON, return the conditions dict or None."""
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error("Failed to fetch current conditions: %s", e)
        return None

    try:
        current = data["current"]
        daily = data["daily"]
        hourly = data["hourly"]

        # Find the hourly index that corresponds to the current hour
        cur_time_prefix = current.get("time", "")[:13]  # e.g. "2026-02-25T23"
        hourly_times = hourly.get("time", [])
        cur_idx = next((i for i, t in enumerate(hourly_times) if t[:13] == cur_time_prefix), -1)

        # Visibility: current hour in meters → miles
        visibility_list = hourly.get("visibility") or []
        visibility_m = (visibility_list[cur_idx] if cur_idx >= 0 and visibility_list else 0) or 0
        visibility_mi = round(visibility_m / 1609.34, 1)

        # Rain: today = last daily entry; 7-day = sum of all entries
        precip_list = daily.get("precipitation_sum", [])
        rain_today = precip_list[-1] if precip_list else 0.0
        rain_7day = sum(p for p in precip_list if p is not None)

        # Sunrise / Sunset — astral gives second-precision local times and exact delta
        sunrise_list = daily.get("sunrise", [])
        sunset_list  = daily.get("sunset", [])
        tz_name      = data.get("timezone", "UTC")
        # Fallback to Open-Meteo minute-precision if astral fails
        sunrise = _parse_time(sunrise_list[-1]) if sunrise_list else "N/A"
        sunset  = _parse_time(sunset_list[-1])  if sunset_list  else "N/A"
        sunrise_delta = ""
        sunset_delta  = ""
        try:
            _tz  = ZoneInfo(tz_name)
            _loc = LocationInfo(latitude=lat, longitude=lon)
            _today = _date.today()
            _s_td  = _astral_sun(_loc.observer, date=_today,            tzinfo=_tz)
            _s_yd  = _astral_sun(_loc.observer, date=_today - _td(days=1), tzinfo=_tz)
            sunrise = _s_td["sunrise"].strftime("%-I:%M %p")
            sunset  = _s_td["sunset"].strftime("%-I:%M %p")
            def _delta_str(a, b):
                # Compare time-of-day only — a/b are datetimes on different dates
                a_s = a.hour * 3600 + a.minute * 60 + a.second
                b_s = b.hour * 3600 + b.minute * 60 + b.second
                d   = a_s - b_s
                sign = "+" if d >= 0 else "-"
                s = abs(d)
                return f"{sign}{s // 60}:{s % 60:02d}"
            sunrise_delta = _delta_str(_s_td["sunrise"], _s_yd["sunrise"])
            sunset_delta  = _delta_str(_s_td["sunset"],  _s_yd["sunset"])
        except Exception as e:
            logger.warning("astral sunrise/sunset error: %s", e)

        # High / Low today
        high_today = int(round(daily.get("temperature_2m_max", [0])[-1] or 0))
        low_today = int(round(daily.get("temperature_2m_min", [0])[-1] or 0))

        # Moon phase (pure math, no network)
        age = _moon_age()
        fraction = _phase_fraction(age)
        moon_name = _phase_name(fraction)
        moon_illum = _illumination(fraction)

        # Pressure: hPa → inHg + trend from last 3 hourly values
        pressure_hpa = current.get("surface_pressure", 1013.25)
        pressure_inhg = round(pressure_hpa * 0.02953, 2)
        pressure_hourly = hourly.get("surface_pressure", [])
        if cur_idx >= 3 and pressure_hourly and pressure_hourly[cur_idx] and pressure_hourly[cur_idx - 3]:
            pdiff = pressure_hourly[cur_idx] - pressure_hourly[cur_idx - 3]
            pressure_trend = "↑" if pdiff > 0.8 else ("↓" if pdiff < -0.8 else "→")
        else:
            pressure_trend = ""

        # Humidity trend from last 3 hourly values
        humidity_hourly = hourly.get("relative_humidity_2m", [])
        if cur_idx >= 3 and humidity_hourly and humidity_hourly[cur_idx] is not None and humidity_hourly[cur_idx - 3] is not None:
            hdiff = humidity_hourly[cur_idx] - humidity_hourly[cur_idx - 3]
            humidity_trend = "↑" if hdiff > 2 else ("↓" if hdiff < -2 else "→")
        else:
            humidity_trend = ""

        # Wind direction
        wind_dir = _deg_to_compass(current.get("wind_direction_10m", 0))

        # Weather description
        weather_code = current.get("weather_code", 0)
        weather_desc = _wmo_description(weather_code)

        # Next 5 hourly slots after current hour
        hourly_temps  = hourly.get("temperature_2m", [])
        hourly_codes  = hourly.get("weather_code", [])
        hourly_precip = hourly.get("precipitation_probability", [])
        hourly_uv     = hourly.get("uv_index", [])
        hourly_forecast = []
        for i in range(1, 6):
            idx = cur_idx + i
            if 0 <= idx < len(hourly_times):
                try:
                    slot_dt = _dt.fromisoformat(hourly_times[idx])
                    label = slot_dt.strftime("%-I %p")
                except Exception:
                    label = hourly_times[idx][-5:]
                temp_h   = int(round(hourly_temps[idx]))  if idx < len(hourly_temps)  else None
                code_h   = hourly_codes[idx]               if idx < len(hourly_codes)  else 0
                precip_h = hourly_precip[idx]              if idx < len(hourly_precip) else 0
                uv_h     = hourly_uv[idx]                  if idx < len(hourly_uv)     else 0
                hourly_forecast.append({
                    "time":   label,
                    "temp":   temp_h,
                    "desc":   _wmo_description(code_h),
                    "precip": int(precip_h) if precip_h is not None else 0,
                    "uv":     round(float(uv_h), 1) if uv_h is not None else 0.0,
                })

        return {
            "temp":         int(round(current.get("temperature_2m", 0))),
            "feels_like":   int(round(current.get("apparent_temperature", 0))),
            "humidity":     int(current.get("relative_humidity_2m", 0)),
            "weather_code": weather_code,
            "weather_desc": weather_desc,
            "is_day":       bool(current.get("is_day", 1)),
            "pressure":        pressure_inhg,
            "pressure_trend":  pressure_trend,
            "humidity_trend":  humidity_trend,
            "wind_speed":   int(round(current.get("wind_speed_10m", 0))),
            "wind_dir":     wind_dir,
            "wind_gust":    int(round(current.get("wind_gusts_10m", 0))),
            "uv_index":     round(current.get("uv_index", 0), 1),
            "visibility":   visibility_mi,
            "rain_today":   round(rain_today, 2) if rain_today else 0.0,
            "rain_7day":    round(rain_7day, 2),
            "sunrise":       sunrise,
            "sunset":        sunset,
            "sunrise_delta": sunrise_delta,
            "sunset_delta":  sunset_delta,
            "high_today":   high_today,
            "low_today":    low_today,
            "moon_name":    moon_name,
            "moon_illum":   moon_illum,
            "hourly_forecast": hourly_forecast,
        }

    except Exception as e:
        logger.exception("Error parsing conditions response")
        return None


def fetch_current_conditions(lat: float, lon: float, headers: dict) -> Optional[Dict[str, Any]]:
    """
    Fetch current weather conditions from Open-Meteo (no API key required).
    Results are cached for _CONDITIONS_TTL seconds.

    On failure: immediately returns the last known-good result (stale) so that
    a transient conditions outage never delays the radar image.  The radar and
    conditions data can legitimately be different ages — that is fine.
    """
    global _conditions_cache, _last_good_conditions
    now = time.time()
    if _conditions_cache["data"] is not None and now - _conditions_cache["ts"] < _CONDITIONS_TTL:
        return _conditions_cache["data"]

    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
        f"weather_code,surface_pressure,"
        f"wind_speed_10m,wind_direction_10m,wind_gusts_10m,uv_index,is_day"
        f"&hourly=visibility,surface_pressure,relative_humidity_2m,temperature_2m,weather_code,precipitation_probability,uv_index"
        f"&daily=sunrise,sunset,precipitation_sum,temperature_2m_max,temperature_2m_min"
        f"&wind_speed_unit=mph"
        f"&temperature_unit=fahrenheit"
        f"&precipitation_unit=inch"
        f"&timezone=auto"
        f"&past_days=7"
        f"&forecast_days=2"
    )

    result = _do_fetch_conditions(url, lat, lon, headers)

    if result is None:
        logger.warning("Conditions fetch failed — retrying once...")
        result = _do_fetch_conditions(url, lat, lon, headers)

    if result is None:
        stale = _last_good_conditions.get("data")
        if stale is not None:
            stale_ts = _last_good_conditions.get("ts", 0)
            stale_label = _dt.fromtimestamp(stale_ts).strftime("%-I:%M %p") if stale_ts else "unknown"
            logger.warning(
                "Conditions still unavailable — displaying last known-good data from %s.", stale_label
            )
            stale = dict(stale)
            stale["stale_as_of"] = stale_label
        else:
            logger.error("Conditions unavailable and no cached data to fall back on.")
        return stale

    # Success — update both the TTL cache and the persistent last-good store
    _conditions_cache["data"] = result
    _conditions_cache["ts"] = now
    _last_good_conditions["data"] = result
    _last_good_conditions["ts"] = now
    logger.info("Conditions fetched: %d°F, %s", result["temp"], result["weather_desc"])
    return result


def _draw_panel_moon(draw, cx, cy, r, fraction):
    """Draw a simple e-ink-friendly moon phase disc on a PIL ImageDraw.

    fraction: 0.0 = new moon, 0.5 = full moon.
    Waxing phases illuminate the right side; waning illuminate the left.
    """
    B = (0, 0, 0)
    W = (255, 255, 255)
    bb = [cx - r, cy - r, cx + r, cy + r]

    if fraction < 0.0625 or fraction > 0.9375:
        # New moon: solid white disc with black outline
        draw.ellipse(bb, fill=W, outline=B)
        return
    if 0.4375 < fraction < 0.5625:
        # Full moon: solid white disc with black outline
        draw.ellipse(bb, fill=W, outline=B)
        return

    # Terminator x-radius; 0 = straight vertical (quarter), r = full (crescent)
    tx = int(r * abs(math.cos(fraction * 2 * math.pi)))

    if fraction < 0.5:
        # Waxing: right side lit (white), left side shadow (black)
        draw.ellipse(bb, fill=B)                  # full disc black
        draw.chord(bb, -90, 90, fill=W)           # right half-disc white (chord stays within circle)
        if tx > 0:
            if fraction <= 0.25:
                # Crescent: black terminator narrows the lit crescent on the right
                draw.ellipse([cx - tx, cy - r, cx + tx, cy + r], fill=B)
            else:
                # Gibbous: white terminator extends lit area into left, leaving thin shadow sliver
                draw.ellipse([cx - tx, cy - r, cx + tx, cy + r], fill=W)
    else:
        # Waning: left side lit (white), right side shadow (black)
        draw.ellipse(bb, fill=B)                  # full disc black
        draw.chord(bb, 90, 270, fill=W)           # left half-disc white (chord stays within circle)
        if tx > 0:
            if fraction < 0.75:
                # Gibbous: white terminator extends lit area into right, leaving thin shadow sliver
                draw.ellipse([cx - tx, cy - r, cx + tx, cy + r], fill=W)
            else:
                # Crescent: black terminator narrows the lit crescent on the left
                draw.ellipse([cx - tx, cy - r, cx + tx, cy + r], fill=B)

    draw.ellipse(bb, outline=B)


def draw_conditions_panel(canvas, conditions, config, panel_x, panel_w, header_h=30, qr_url=None):
    """
    Draw the current-conditions data panel on an existing PIL Image.
    panel_x:  left edge of the panel in canvas pixels
    panel_w:  width of the panel in pixels
    header_h: vertical offset where content starts (header drawn externally)
    """
    draw = ImageDraw.Draw(canvas)
    height = canvas.size[1]
    margin = 8
    text_x = panel_x + margin
    right_x = panel_x + panel_w - margin
    text_w = right_x - text_x
    font_path = config.get("font_path", "")
    BLACK = (0, 0, 0)
    WHITE = (255, 255, 255)

    def _font(size):
        """Load bold font, falling back through a platform-safe chain."""
        for path in [
            config.get("bold_font_path", ""),
            "/System/Library/Fonts/Supplemental/DIN Alternate Bold.ttf",  # macOS
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",           # macOS fallback
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",# Pi
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",        # Pi fallback
            font_path,
        ]:
            if path:
                try:
                    return ImageFont.truetype(path, size)
                except Exception:
                    continue
        return ImageFont.load_default()

    def _separator(y):
        draw.line([(text_x, y), (right_x, y)], fill=BLACK, width=1)
        return y + 5

    def _trend_shape(x, y, trend, row_h):
        """Draw a small filled trend triangle using PIL primitives (no unicode needed)."""
        r = max(5, row_h // 4)
        cy = y + row_h // 2
        if trend == "up":
            draw.polygon([(x + r, cy - r), (x, cy + r), (x + 2*r, cy + r)], fill=BLACK)
        elif trend == "down":
            draw.polygon([(x + r, cy + r), (x, cy - r), (x + 2*r, cy - r)], fill=BLACK)
        else:  # steady
            draw.rectangle([x, cy - 2, x + 2*r, cy + 2], fill=BLACK)
        return 2*r + 4  # pixel width consumed

    if conditions is None:
        draw.text((panel_x + panel_w // 2, height // 2), "No data",
                  fill=BLACK, font=_font(18), anchor="mm")
        return

    y = header_h + 6
    temp_str = f"{conditions['temp']}°F"
    feels_desc = f"Feels like {conditions['feels_like']}°F  \u2022  {conditions['weather_desc']}"

    if qr_url:
        # Pass 1: measure both rows at full text_w to get total height for QR size
        for size in range(72, 55, -2):
            f_temp = _font(size)
            if draw.textbbox((0, 0), temp_str, font=f_temp)[2] <= text_w:
                break
        for size in range(15, 8, -1):
            f_feels = _font(size)
            if draw.textbbox((0, 0), feels_desc, font=f_feels)[2] <= text_w:
                break
        t_h = draw.textbbox((0, 0), temp_str, font=f_temp)[3]
        f_h = draw.textbbox((0, 0), feels_desc, font=f_feels)[3]
        qr_size = config.get('panel_qr_size', 0) or (t_h + 4 + f_h)  # fixed override or auto from row heights
        top_text_w = text_w - qr_size - 4

        # Pass 2: refit both rows into the narrower width
        for size in range(72, 55, -2):
            f_temp = _font(size)
            if draw.textbbox((0, 0), temp_str, font=f_temp)[2] <= top_text_w:
                break
        for size in range(15, 8, -1):
            f_feels = _font(size)
            if draw.textbbox((0, 0), feels_desc, font=f_feels)[2] <= top_text_w:
                break
    else:
        qr_size = 0
        top_text_w = text_w
        for size in range(72, 55, -2):
            f_temp = _font(size)
            if draw.textbbox((0, 0), temp_str, font=f_temp)[2] <= text_w:
                break
        for size in range(15, 8, -1):
            f_feels = _font(size)
            if draw.textbbox((0, 0), feels_desc, font=f_feels)[2] <= text_w:
                break

    # Temperature
    draw.text((text_x, y), temp_str, fill=BLACK, font=f_temp)
    temp_h = draw.textbbox((0, 0), temp_str, font=f_temp)[3]
    y += temp_h + 4

    # Paste QR filling temperature + feels-like height
    if qr_url:
        try:
            qr_img = qrcode.make(qr_url).convert("RGB")
            qr_img = qr_img.resize((qr_size, qr_size), Image.LANCZOS)
            qr_x = panel_x + panel_w - margin - qr_size - config.get('panel_qr_x_offset', 0)
            qr_y = header_h + 6 + config.get('panel_qr_y_offset', 0)
            canvas.paste(qr_img, (qr_x, qr_y))
        except Exception as e:
            logger.error("QR code error: %s", e)

    # Feels like / description
    draw.text((text_x, y), feels_desc, fill=BLACK, font=f_feels)
    y += draw.textbbox((0, 0), feels_desc, font=f_feels)[3] + 4

    # Stale data notice
    stale_as_of = conditions.get("stale_as_of")
    if stale_as_of:
        stale_str = f"Stale data as of {stale_as_of}"
        stale_font = _font(11)
        draw.text((text_x, y), stale_str, fill=BLACK, font=stale_font)
        y += draw.textbbox((0, 0), stale_str, font=stale_font)[3] + 2

    # Separator
    y = _separator(y)

    # Two-column data rows — pressure row gets a drawn trend arrow
    label_font = _font(17)
    value_font = _font(20)
    row_h = 24
    def _trend_dir(arrow):
        return "up" if arrow == "↑" else ("down" if arrow == "↓" else ("steady" if arrow == "→" else ""))

    rows = [
        ("Humidity",   f"{conditions['humidity']}%",       _trend_dir(conditions.get("humidity_trend", ""))),
        ("Pressure",   f"{conditions['pressure']} inHg",   _trend_dir(conditions.get("pressure_trend", ""))),
        ("Visibility", f"{conditions['visibility']} mi",                                          ""),
        ("UV Index",   f"{conditions['uv_index']}",                                               ""),
        ("Wind",       f"{conditions['wind_dir']} {conditions['wind_speed']} / G{conditions['wind_gust']} mph", ""),
        ("Rain Today", f"{conditions['rain_today']}\"",                                           ""),
        ("Rain 7-Day", f"{conditions['rain_7day']}\"",                                            ""),
    ]

    for label, value, row_trend in rows:
        draw.text((text_x, y + 2), label, fill=BLACK, font=label_font)
        val_bbox = draw.textbbox((0, 0), value, font=value_font)
        val_w = val_bbox[2] - val_bbox[0]
        if row_trend:
            r = max(5, row_h // 4)
            shape_w = 2*r + 4
            shape_x = right_x - shape_w
            draw.text((shape_x - val_w - 4, y), value, fill=BLACK, font=value_font)
            _trend_shape(shape_x, y, row_trend, row_h)
        else:
            draw.text((right_x - val_w, y), value, fill=BLACK, font=value_font)
        y += row_h

    # Separator
    y = _separator(y)

    # Sunrise / Sunset rows with delta from yesterday
    sun_font   = _font(20)
    delta_font = _font(13)
    sun_rows = [
        ("Sunrise", conditions["sunrise"], conditions.get("sunrise_delta", "")),
        ("Sunset",  conditions["sunset"],  conditions.get("sunset_delta", "")),
    ]
    for label, value, delta in sun_rows:
        draw.text((text_x, y), label, fill=BLACK, font=sun_font)
        val_bbox = draw.textbbox((0, 0), value, font=sun_font)
        val_w = val_bbox[2] - val_bbox[0]
        draw.text((right_x - val_w, y), value, fill=BLACK, font=sun_font)
        if delta:
            d_bb = draw.textbbox((0, 0), delta, font=delta_font)
            d_w  = d_bb[2] - d_bb[0]
            d_h  = d_bb[3] - d_bb[1]
            # Right-align delta just left of the time value
            dx = right_x - val_w - d_w - 5
            dy = y + (row_h - d_h) // 2
            draw.text((dx, dy), delta, fill=BLACK, font=delta_font)
        y += row_h

    # Separator
    y = _separator(y)

    # Moon phase name + illumination, with drawn moon disc on the right (spans 2 rows)
    moon_font = _font(17)
    moon_str  = f"{conditions['moon_name']}  {conditions['moon_illum']}%"
    hl_str    = f"Today  H{conditions['high_today']}\u00b0 / L{conditions['low_today']}\u00b0"
    two_row_h = 2 * row_h
    moon_r    = max(8, (two_row_h - 6) // 2)
    moon_cx   = right_x - moon_r - 1
    moon_cy   = y + two_row_h // 2 - 3

    draw.text((text_x, y), moon_str, fill=BLACK, font=moon_font)
    y += row_h
    draw.text((text_x, y), hl_str, fill=BLACK, font=moon_font)
    y += row_h

    age_val  = _moon_age()
    frac_val = _phase_fraction(age_val)
    _draw_panel_moon(draw, moon_cx, moon_cy, moon_r, frac_val)

    return y


def _uv_color(uv: float):
    """Return e-ink-safe RGB color for UV index level. None = no indicator (UV=0)."""
    if uv <= 0:
        return None
    elif uv <= 2:
        return (0, 200, 0)    # Green - Low
    elif uv <= 5:
        return (255, 220, 0)  # Yellow - Moderate
    elif uv <= 7:
        return (255, 128, 0)  # Orange - High
    else:
        return (220, 0, 0)    # Red - Very High/Extreme


def _draw_hourly_uv_boxes(canvas, conditions, config, panel_x, panel_w, start_y):
    """Draw 5-hour forecast boxes with UV color coding. Call AFTER the B/W panel snap."""
    draw = ImageDraw.Draw(canvas)
    height = canvas.size[1]
    margin = 8
    text_x = panel_x + margin
    right_x = panel_x + panel_w - margin
    text_w = right_x - text_x
    BLACK = (0, 0, 0)

    def _font(size):
        font_path = config.get("font_path", "")
        for path in [
            config.get("bold_font_path", ""),
            "/System/Library/Fonts/Supplemental/DIN Alternate Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            font_path,
        ]:
            if path:
                try:
                    return ImageFont.truetype(path, size)
                except Exception:
                    continue
        return ImageFont.load_default()

    hourly = conditions.get("hourly_forecast", [])
    if not hourly:
        return

    # Separator line
    draw.line([(text_x, start_y), (right_x, start_y)], fill=BLACK, width=1)
    y = start_y + 5

    box_count = min(5, len(hourly))
    if box_count <= 0 or y >= height - 20:
        return

    UV_STRIPE_H = 4
    box_h = height - y - 2
    box_w = text_w // box_count
    inner_w = box_w - 8
    inner_h = box_h - 6

    actual_times = [s["time"] for s in hourly[:box_count]]
    actual_descs = [s["desc"] for s in hourly[:box_count]]
    longest_time = max(actual_times, key=len) if actual_times else "12 AM"
    longest_desc = max(actual_descs, key=len) if actual_descs else ""

    best_base = 8
    for base_sz in range(24, 7, -1):
        temp_sz = max(base_sz, int(base_sz * 1.8))
        bf = _font(base_sz)
        tf = _font(temp_sz)
        lh_base = draw.textbbox((0, 0), "Ag", font=bf)[3]
        lh_temp = draw.textbbox((0, 0), "Ag", font=tf)[3]
        total_h = lh_temp + 2 * lh_base + 8 + UV_STRIPE_H
        max_w = max(
            draw.textbbox((0, 0), "100°", font=tf)[2],
            draw.textbbox((0, 0), longest_desc, font=bf)[2],
        )
        if total_h <= inner_h and max_w <= inner_w:
            best_base = base_sz
            break

    bf = _font(best_base)
    tf = _font(max(best_base, int(best_base * 1.8)))
    lh_base = draw.textbbox((0, 0), "Ag", font=bf)[3]
    lh_temp = draw.textbbox((0, 0), "Ag", font=tf)[3]

    for i, slot in enumerate(hourly[:box_count]):
        bx = text_x + i * box_w
        by = y
        uv_val = slot.get("uv", 0) or 0
        uv_col = _uv_color(uv_val)

        draw.rectangle([(bx, by), (bx + box_w - 2, by + box_h - 1)], outline=BLACK, width=1)

        def _center_text(text, font, top_y, bx=bx):
            w = draw.textbbox((0, 0), text, font=font)[2]
            draw.text((bx + (box_w - w) // 2, top_y), text, fill=BLACK, font=font)

        # Temp centered vertically
        cy_temp = by + (box_h - lh_temp) // 2
        _center_text(f"{slot['temp']}°", tf, cy_temp)

        # Time top-left
        draw.text((bx + 3, by + 2), slot["time"], fill=BLACK, font=bf)

        # Precip % top-right
        if slot["precip"]:
            prec_str = f"{slot['precip']}%"
            prec_w = draw.textbbox((0, 0), prec_str, font=bf)[2]
            draw.text((bx + box_w - 2 - prec_w - 2, by + 2), prec_str, fill=BLACK, font=bf)

        # Description pinned to bottom (above UV stripe)
        desc = slot["desc"]
        while desc and draw.textbbox((0, 0), desc, font=bf)[2] > inner_w:
            desc = desc[:-1]
        _center_text(desc, bf, by + box_h - lh_base - UV_STRIPE_H - 3)

        # UV color stripe at very bottom of box
        if uv_col:
            stripe_y = by + box_h - UV_STRIPE_H
            draw.rectangle(
                [(bx + 1, stripe_y), (bx + box_w - 2, by + box_h - 1)],
                fill=uv_col,
            )


if platform.system() == "Linux":
    from display import display_color_image


STATE_FILE = os.path.join("radar", "radar_state.json")


def load_state(state_file):
    if os.path.exists(state_file):
        with open(state_file, "r") as f:
            return json.load(f)
    return None


def save_state(state_file, state):
    with open(state_file, "w") as f:
        json.dump(state, f)


def images_are_equal(img1, img2):
    if img1.mode != img2.mode or img1.size != img2.size:
        return False
    return list(img1.getdata()) == list(img2.getdata())


def distance(c1, c2):
    return math.sqrt((c1[0] - c2[0])**2 + (c1[1] - c2[1])**2 + (c1[2] - c2[2])**2)


def quantize_to_seven_colors(input_path, output_path, more_colors, threshold=0):
    white = (255, 255, 255)

    if more_colors:
        palette_5 = [
            (255, 204, 204), (255, 102, 102), (255, 0, 0), (153, 0, 0), (102, 0, 0),
            (204, 255, 204), (102, 255, 102), (0, 255, 0), (0, 153, 0), (0, 102, 0),
            (204, 204, 255), (102, 102, 255), (0, 0, 255), (0, 0, 153), (0, 0, 102),
            (255, 255, 204), (255, 255, 102), (255, 255, 0), (204, 204, 0), (153, 153, 0),
            (255, 224, 192), (255, 178, 102), (255, 128, 0), (204, 102, 0), (153, 76, 0),
            (0, 0, 0),
        ]
    else:
        palette_5 = [
            (255, 0, 0),
            (0, 255, 0),
            (0, 0, 255),
            (255, 255, 0),
            (255, 128, 0),
            (0, 0, 0),
        ]

    original = Image.open(input_path).convert("RGB")
    pixels = original.load()
    width, height = original.size
    for y in range(height):
        for x in range(width):
            p = pixels[x, y]
            if distance(p, white) <= threshold:
                pixels[x, y] = white
            else:
                best_color = min(palette_5, key=lambda color: distance(p, color))
                pixels[x, y] = best_color

    original.save(output_path, format="bmp")
    logger.info("Quantized image saved to %s", output_path)


def _fetch_rainviewer_image(
    lat: float, lon: float, zoom: int, width: int, height: int,
    color_scheme: int, headers: dict
) -> Tuple[Optional[Image.Image], Optional[int]]:
    """
    Fetch a composite radar image from RainViewer XYZ tiles centered on lat/lon.
    Tiles are transparent PNGs composited onto a white background.
    Returns (RGB PIL Image sized exactly width×height, frame Unix timestamp), or (None, None).
    """
    _TILE_SIZE = 512

    try:
        api_resp = requests.get(
            "https://api.rainviewer.com/public/weather-maps.json",
            headers=headers, timeout=10,
        )
        api_resp.raise_for_status()
        data = api_resp.json()
    except Exception as e:
        logger.error("RainViewer API request failed: %s", e)
        return None, None

    radar_frames = data.get("radar", {}).get("past", [])
    if not radar_frames:
        logger.error("No radar frames in RainViewer API response")
        return None, None
    latest_frame = radar_frames[-1]
    latest_path = latest_frame["path"]  # e.g. "/v2/radar/1721234567"
    frame_ts = latest_frame.get("time")  # Unix timestamp int
    logger.info("RainViewer: using frame %s", latest_path)

    # World-pixel coordinates of the center point at this zoom level
    n = 2 ** zoom
    cx_px = (lon + 180.0) / 360.0 * n * _TILE_SIZE
    lat_rad = math.radians(lat)
    cy_px = (
        (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi)
        / 2.0 * n * _TILE_SIZE
    )

    # Display bounds in world-pixel space
    left_px = cx_px - width / 2.0
    top_px = cy_px - height / 2.0

    # Tile index range needed to cover the display area
    tx_min = int(left_px // _TILE_SIZE)
    ty_min = int(top_px // _TILE_SIZE)
    tx_max = int((left_px + width - 1) // _TILE_SIZE)
    ty_max = int((top_px + height - 1) // _TILE_SIZE)

    canvas_w = (tx_max - tx_min + 1) * _TILE_SIZE
    canvas_h = (ty_max - ty_min + 1) * _TILE_SIZE
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (255, 255, 255, 255))

    for tx in range(tx_min, tx_max + 1):
        for ty in range(ty_min, ty_max + 1):
            url = (
                f"https://tilecache.rainviewer.com{latest_path}"
                f"/{_TILE_SIZE}/{zoom}/{tx}/{ty}/{color_scheme}/1_1.png"
            )
            try:
                resp = requests.get(url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    tile = Image.open(io.BytesIO(resp.content)).convert("RGBA")
                    px = (tx - tx_min) * _TILE_SIZE
                    py = (ty - ty_min) * _TILE_SIZE
                    canvas.paste(tile, (px, py), tile)
                else:
                    logger.warning("RainViewer tile %d/%d HTTP %d", tx, ty, resp.status_code)
            except Exception as e:
                logger.warning("RainViewer tile %d/%d fetch failed: %s", tx, ty, e)

    crop_x = int(left_px - tx_min * _TILE_SIZE)
    crop_y = int(top_px - ty_min * _TILE_SIZE)
    result = canvas.crop((crop_x, crop_y, crop_x + width, crop_y + height))
    return result.convert("RGB"), frame_ts


def _fetch_radar_image(radar_url: str, headers: dict):
    """
    Fetch a radar GIF from *radar_url* with up to 3 quick retries (2 s apart).
    If all quick retries fail, waits _RETRY_WAIT seconds and makes one final attempt.
    Returns the successful requests.Response, or None on total failure.
    """
    def _try_once():
        for attempt in range(3):
            try:
                resp = requests.get(radar_url, headers=headers, timeout=10)
            except Exception as e:
                logger.warning("Radar request error (attempt %d): %s", attempt + 1, e)
                if attempt < 2:
                    time.sleep(2)
                continue
            if resp.status_code == 200:
                return resp
            if resp.status_code == 404 and attempt < 2:
                logger.warning("Radar 404 (attempt %d), retrying in 2s...", attempt + 1)
                time.sleep(2)
            else:
                logger.warning("Radar HTTP %d on attempt %d", resp.status_code, attempt + 1)
                return None  # non-retriable error
        return None

    response = _try_once()
    if response is not None:
        return response

    # First pass failed — wait and try once more before giving up
    logger.warning(
        "Radar fetch failed on all quick retries. Waiting %ds before final attempt...",
        _RETRY_WAIT,
    )
    time.sleep(_RETRY_WAIT)
    response = _try_once()
    if response is not None:
        logger.info("Radar fetch succeeded after %ds wait.", _RETRY_WAIT)
        return response

    logger.error("Radar fetch failed entirely after retry wait.")
    return None


_EINK_WHITE  = [255, 255, 255]
_EINK_BLACK  = [  0,   0,   0]
_EINK_BLUE   = [  0,   0, 255]
_EINK_GREEN  = [  0, 255,   0]
_EINK_YELLOW = [255, 255,   0]
_EINK_ORANGE = [255, 128,   0]
_EINK_RED    = [255,   0,   0]

_SEVEN_COLOR_LEGEND = [
    # (swatch RGB,          label,       text RGB for readability)
    ((255, 255, 255), "None",    (0,   0,   0)),
    ((0,   0,   255), "~5-25",   (255, 255, 255)),
    ((0,   255,   0), "~25-35",  (0,   0,   0)),
    ((255, 255,   0), "~35-45",  (0,   0,   0)),
    ((255, 128,   0), "~45-50",  (255, 255, 255)),
    ((255,   0,   0), "~50-60",  (255, 255, 255)),
    ((0,   0,     0), ">60dBZ",  (255, 255, 255)),
]

_LEGEND_H = 36  # height of the bottom legend strip in pixels


def _remap_radar_seven_color(image: Image.Image) -> Image.Image:
    """
    Remap radar pixels to exactly the 7 Waveshare e-ink display colors via
    HSV-based precipitation-intensity classification.

    dBZ tiers (approximate, using RainViewer NWS Reflectivity scheme):
      White  = background / no precipitation
      Blue   = ~5-25 dBZ  (trace / scattered)
      Green  = ~25-35 dBZ (light rain)
      Yellow = ~35-45 dBZ (moderate rain)
      Orange = ~45-50 dBZ (heavy rain)
      Red    = ~50-60 dBZ (severe)
      Black  = >60 dBZ    (extreme / potential hail)
    """
    arr = np.array(image.convert("RGB")).astype(np.float32)
    r = arr[:, :, 0] / 255.0
    g = arr[:, :, 1] / 255.0
    b = arr[:, :, 2] / 255.0

    cmax = np.maximum(np.maximum(r, g), b)
    cmin = np.minimum(np.minimum(r, g), b)
    delta = cmax - cmin

    v = cmax
    s = np.where(cmax > 1e-6, delta / cmax, 0.0)

    # Hue in [0, 1): 0/1=red, 1/6=yellow, 2/6=green, 3/6=cyan, 4/6=blue, 5/6=magenta
    h = np.zeros_like(r)
    m_r = (cmax == r) & (delta > 1e-6)
    m_g = (cmax == g) & (delta > 1e-6)
    m_b = (cmax == b) & (delta > 1e-6)
    h[m_r] = (((g[m_r] - b[m_r]) / delta[m_r]) % 6) / 6.0
    h[m_g] = ((b[m_g] - r[m_g]) / delta[m_g] + 2.0) / 6.0
    h[m_b] = ((r[m_b] - g[m_b]) / delta[m_b] + 4.0) / 6.0

    # Start all white (background)
    out = np.full(arr.shape, 255, dtype=np.uint8)

    # Blue: hue 160°-300° (0.444-0.833) — cyans and blues (light precip)
    m = (h >= 0.444) & (h < 0.833) & (s > 0.25) & (v > 0.35)
    out[m] = _EINK_BLUE

    # Green: hue 80°-160° (0.222-0.444) — greens (light-moderate)
    m = (h >= 0.222) & (h < 0.444) & (s > 0.25) & (v > 0.35)
    out[m] = _EINK_GREEN

    # Yellow: hue 60°-80° (0.167-0.222) — yellows (moderate)
    m = (h >= 0.167) & (h < 0.222) & (s > 0.25) & (v > 0.35)
    out[m] = _EINK_YELLOW

    # Orange: hue 30°-60° (0.083-0.167) — oranges (heavy)
    m = (h >= 0.083) & (h < 0.167) & (s > 0.25) & (v > 0.35)
    out[m] = _EINK_ORANGE

    # Red: hue 0°-30° or 300°-360° (0-0.083 or 0.833-1.0) — reds and magentas (severe)
    m = ((h < 0.083) | (h >= 0.833)) & (s > 0.25) & (v > 0.35)
    out[m] = _EINK_RED

    # Black: very dark pixels OR dark-but-saturated (extreme / hail markers).
    # Written last so it overrides any prior color assignment for dark pixels.
    m = (v < 0.18) | ((s > 0.20) & (v <= 0.35))
    out[m] = _EINK_BLACK

    return Image.fromarray(out, "RGB")


def _draw_seven_color_legend(
    canvas: Image.Image,
    frame_ts: Optional[int],
    station: str,
    config: dict,
) -> None:
    """Draw the bottom legend strip showing all 7 e-ink colors with dBZ labels."""
    W, H = canvas.size
    y0 = H - _LEGEND_H

    draw = ImageDraw.Draw(canvas)
    draw.rectangle([(0, y0), (W - 1, H - 1)], fill=(255, 255, 255))
    draw.line([(0, y0), (W - 1, y0)], fill=(0, 0, 0), width=1)

    def _lfont(size):
        for path in [
            config.get("bold_font_path", ""),
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            config.get("font_path", ""),
        ]:
            if path:
                try:
                    return ImageFont.truetype(path, size)
                except Exception:
                    continue
        return ImageFont.load_default()

    font = _lfont(11)
    swatch_side = 20
    pad = 6
    text_pad = 3
    swatch_y = y0 + (_LEGEND_H - swatch_side) // 2

    x = 8
    for swatch_rgb, label, text_rgb in _SEVEN_COLOR_LEGEND:
        # Color swatch with black outline
        draw.rectangle(
            [(x, swatch_y), (x + swatch_side - 1, swatch_y + swatch_side - 1)],
            fill=swatch_rgb, outline=(0, 0, 0),
        )
        # Label to the right of swatch
        bb = draw.textbbox((0, 0), label, font=font)
        lh = bb[3] - bb[1]
        draw.text(
            (x + swatch_side + text_pad, swatch_y + (swatch_side - lh) // 2),
            label, fill=(0, 0, 0), font=font,
        )
        x += swatch_side + text_pad + (bb[2] - bb[0]) + pad

    # Right side: station + frame timestamp
    if frame_ts:
        try:
            from datetime import timezone as _tz_mod
            ts_str = _dt.fromtimestamp(int(frame_ts), tz=_tz_mod.utc).astimezone().strftime("%-I:%M %p")
        except Exception:
            ts_str = ""
    else:
        ts_str = ""

    right_str = f"{station}  {ts_str}" if ts_str else station
    rb = draw.textbbox((0, 0), right_str, font=font)
    rw, rh = rb[2] - rb[0], rb[3] - rb[1]
    draw.text(
        (W - rw - 8, y0 + (_LEGEND_H - rh) // 2),
        right_str, fill=(0, 0, 0), font=font,
    )


def _fetch_nws_alerts(lat: float, lon: float, headers: dict) -> List[Dict[str, Any]]:
    """
    Fetch active NWS alerts affecting a point from api.weather.gov.

    Returns a list of {"event", "severity", "polygon"} dicts, where polygon is a
    list of (lon, lat) vertices (or None if the alert has no storm-based geometry).
    Returns [] on any failure — the overlay is best-effort and never blocks radar.
    """
    url = f"https://api.weather.gov/alerts/active?point={lat:.4f},{lon:.4f}"
    nws_headers = dict(headers)
    nws_headers["Accept"] = "application/geo+json"
    try:
        resp = requests.get(url, headers=nws_headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("NWS alerts fetch failed: %s", e)
        return []

    alerts = []
    for feature in data.get("features", []):
        props = feature.get("properties", {}) or {}
        geom = feature.get("geometry") or {}
        polygon = None
        if geom.get("type") == "Polygon":
            rings = geom.get("coordinates") or []
            if rings and rings[0]:
                polygon = [(pt[0], pt[1]) for pt in rings[0]]
        alerts.append({
            "event": props.get("event", "Alert"),
            "severity": props.get("severity", ""),
            "polygon": polygon,
        })
    if alerts:
        logger.info("NWS: %d active alert(s) at point", len(alerts))
    return alerts


def _overlay_severe_alerts(
    canvas: Image.Image, center_lat: float, center_lon: float, zoom: int,
    region_w: int, region_h: int, headers: dict,
    x_off: int = 0, y_off: int = 0,
) -> None:
    """
    Draw active NWS storm-based warning polygons over a RainViewer-projected radar
    region. Uses the same Web-Mercator tile projection as _fetch_rainviewer_image so
    polygons line up with the radar tiles. Best-effort: any failure is swallowed.

    Alerts without polygon geometry (zone-based) are skipped here — the existing
    special-weather banner already surfaces those.
    """
    _TILE_SIZE = 512
    alerts = _fetch_nws_alerts(center_lat, center_lon, headers)
    if not alerts:
        return

    n = 2 ** zoom

    def _world_px(lon: float, lat: float):
        x = (lon + 180.0) / 360.0 * n * _TILE_SIZE
        lat_rad = math.radians(lat)
        y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n * _TILE_SIZE
        return x, y

    cx_px, cy_px = _world_px(center_lon, center_lat)

    def _to_pixel(lon: float, lat: float):
        wx, wy = _world_px(lon, lat)
        return (x_off + region_w / 2.0 + (wx - cx_px),
                y_off + region_h / 2.0 + (wy - cy_px))

    draw = ImageDraw.Draw(canvas)
    RED = (255, 0, 0)

    for alert in alerts:
        poly = alert.get("polygon")
        if not poly or len(poly) < 3:
            continue
        pts = [_to_pixel(lon, lat) for lon, lat in poly]
        # Skip if the polygon is entirely outside the visible region
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        if max(xs) < x_off or min(xs) > x_off + region_w or max(ys) < y_off or min(ys) > y_off + region_h:
            continue
        # Bold red outline
        draw.line(pts + [pts[0]], fill=RED, width=3)
        # Event label at the polygon's top vertex
        label = alert.get("event", "Alert")
        lx = min(xs)
        ly = min(ys)
        lx = max(x_off + 2, min(lx, x_off + region_w - 120))
        ly = max(y_off + 2, min(ly, y_off + region_h - 18))
        try:
            lf = get_font(13, bold=True, config=None)
        except Exception:
            lf = ImageFont.load_default()
        bb = draw.textbbox((0, 0), label, font=lf)
        draw.rectangle([lx, ly, lx + (bb[2] - bb[0]) + 6, ly + (bb[3] - bb[1]) + 4], fill=RED)
        draw.text((lx + 3, ly + 2), label, fill=(255, 255, 255), font=lf)


def generate_weather_image(config, special_msg=None):
    radar_folder = "radar"
    os.makedirs(radar_folder, exist_ok=True)

    width = config.get("width", 800)
    height = config.get("height", 480)
    background_color = config.get("background_color_weather", "white")
    station = config.get("station", {}).get("name", "KTYX")
    location = config.get("station", {}).get("location", "Unknown Location")

    output_path = config.get("output_path") or os.path.join(radar_folder, f"eink_display_{station}.bmp")
    quantized_output_path = config.get("quantized_path") or os.path.join(radar_folder, f"eink_quantized_display_{station}.bmp")

    radar_mode = config.get("radar_mode", "crop").lower()
    final_img = Image.new("RGB", (width, height), color=background_color)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    radar_source = config.get("radar_source", "ridge").lower()
    radar_url_qr = None
    rv_frame_ts = None  # Unix timestamp of the radar frame (RainViewer only)

    if radar_source == "rainviewer":
        forecast_loc = config.get("forecast_location", {})
        rv_lat = config.get("rainviewer_lat") or forecast_loc.get("latitude")
        rv_lon = config.get("rainviewer_lon") or forecast_loc.get("longitude")
        if not rv_lat or not rv_lon:
            logger.error("RainViewer requires lat/lon — set forecast_location or rainviewer_lat/lon")
            return None, False, None
        rv_zoom = config.get("rainviewer_zoom", 8)
        rv_color = config.get("rainviewer_color_scheme", 4)
        # Pre-size to the exact radar canvas so mode scaling is a no-op
        rv_w = width - config.get("panel_width", 280) if radar_mode == "panel" else width
        radar_img, rv_frame_ts = _fetch_rainviewer_image(rv_lat, rv_lon, rv_zoom, rv_w, height, rv_color, headers)
        if radar_img is None:
            logger.error("RainViewer fetch failed — cannot generate radar image.")
            return None, False, None
        if rv_frame_ts:
            _rv_state_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "radar", "rv_frame_state.json")
            try:
                with open(_rv_state_path, "w") as _f:
                    import json as _json
                    _json.dump({"frame_ts": rv_frame_ts}, _f)
            except OSError:
                pass
    else:
        radar_url = f"https://radar.weather.gov/ridge/standard/{station}_0.gif"
        if config.get("url_qr_loop", True):
            radar_url_qr = f"https://radar.weather.gov/ridge/standard/{station}_loop.gif"
        else:
            radar_url_qr = f'https://radar.weather.gov/station/{station.lower()}/standard'

        response = _fetch_radar_image(radar_url, headers)
        if response is None:
            logger.error("Radar image could not be fetched — using last cached result.")
            return None, False, None

        content_type = response.headers.get("Content-Type", "")
        if "image" not in content_type:
            logger.error("Unexpected content type: %s", content_type)
            return None, False, None

        radar_img = Image.open(io.BytesIO(response.content)).convert("RGB")
    primary_region = None

    if radar_mode == "crop":
        scale = max(width / radar_img.width, height / radar_img.height)
        new_w = int(radar_img.width * scale)
        new_h = int(radar_img.height * scale)
        scaled_radar = radar_img.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - width) // 2
        top = (new_h - height) // 2
        processed_radar = scaled_radar.crop((left, top, left + width, top + height))
    elif radar_mode == "fit":
        # Strip 24px NWS title bar (top) and color legend (bottom).
        data_radar = radar_img.crop((0, 24, radar_img.width, radar_img.height - 24))
        scale = min(width / data_radar.width, height / data_radar.height)
        new_w = int(data_radar.width * scale)
        new_h = int(data_radar.height * scale)
        processed_radar = data_radar.resize((new_w, new_h), Image.LANCZOS)
        x_offset = (width - new_w) // 2
        y_offset = (height - new_h) // 2
        primary_region = (x_offset, y_offset, x_offset + new_w, y_offset + new_h)
        final_img.paste(processed_radar, (x_offset, y_offset))
        processed_radar = None
    elif radar_mode == "panel":
        panel_w = config.get("panel_width", 280)
        radar_w = width - panel_w

        header_h = 21

        # Radar fills full height — max scale fills the canvas, clipping ~1px from sides.
        scale = max(radar_w / radar_img.width, height / radar_img.height)
        rw = int(radar_img.width * scale)
        rh = int(radar_img.height * scale)
        scaled_radar = radar_img.resize((rw, rh), Image.LANCZOS)
        x_off = (radar_w - rw) // 2   # negative = PIL auto-crops the sides
        y_off = (height - rh) // 2
        final_img.paste(scaled_radar, (x_off, y_off))

        # White panel background (full height on right side)
        draw_tmp = ImageDraw.Draw(final_img)
        draw_tmp.rectangle([(radar_w, 0), (width - 1, height - 1)], fill="white")

        # Fetch & render conditions (content starts below header_h)
        forecast_loc = config.get("forecast_location", {})
        lat = forecast_loc.get("latitude")
        lon = forecast_loc.get("longitude")
        conditions = fetch_current_conditions(lat, lon, headers) if lat and lon else None
        if special_msg:
            panel_qr = config.get('special_url', "https://forecast.weather.gov/showsigwx.php?warnzone=TNZ027&warncounty=TNC037&firewxzone=TNZ027&local_place1=Nashville%20TN")
        elif config.get('panel_qr_radar', False):
            panel_qr = radar_url_qr
        else:
            panel_qr = None
        hourly_start_y = draw_conditions_panel(final_img, conditions, config, radar_w, panel_w, header_h=header_h, qr_url=panel_qr)

        # Snap only the content area (below header) to pure B/W before drawing header text
        panel_bw = final_img.crop((radar_w, header_h, width, height)).convert("L").point(
            lambda px: 255 if px > 128 else 0
        ).convert("RGB")
        final_img.paste(panel_bw, (radar_w, header_h))

        # Draw colored hourly UV boxes AFTER snap so colors survive quantize
        if conditions and hourly_start_y is not None:
            _draw_hourly_uv_boxes(final_img, conditions, config, radar_w, panel_w, hourly_start_y)

        # Header bar on the RIGHT panel only — drawn last so snap doesn't erode text
        # Red when an alert is active, black otherwise
        draw_tmp = ImageDraw.Draw(final_img)
        header_color = (180, 0, 0) if special_msg else (0, 0, 0)
        draw_tmp.rectangle([(radar_w, 0), (width - 1, header_h - 1)], fill=header_color)

        # Location + weekday text in the header
        current_station = config['station']['name']
        station_loc = (config['station'].get('location') or
                       config.get("panel_header") or forecast_loc.get("name", ""))
        loc_name = f"{current_station}  {station_loc}" if station_loc else current_station
        weekday = _date.today().strftime("%a")
        hdr_str = f"{loc_name}  {weekday}" if loc_name else weekday
        hdr_font = None
        for path in [
            config.get("bold_font_path", ""),
            "/System/Library/Fonts/Supplemental/DIN Alternate Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            config.get("font_path", ""),
        ]:
            if not path:
                continue
            for size in range(20, 9, -1):
                try:
                    f = ImageFont.truetype(path, size)
                    bb = draw_tmp.textbbox((0, 0), hdr_str, font=f)
                    if (bb[2] - bb[0]) <= panel_w - 16 and (bb[3] - bb[1]) <= header_h - 6:
                        hdr_font = f
                        break
                except Exception:
                    break
            if hdr_font:
                break
        if hdr_font is None:
            hdr_font = ImageFont.load_default()
        bb = draw_tmp.textbbox((0, 0), hdr_str, font=hdr_font)
        text_y = (header_h - (bb[3] - bb[1])) // 2
        draw_tmp.text((radar_w + 8, text_y), hdr_str, fill=(255, 255, 255), font=hdr_font)

        # Thin vertical separator (full height)
        draw_tmp.line([(radar_w, 0), (radar_w, height - 1)], fill=(180, 180, 180), width=1)

        # RainViewer frame timestamp — bottom-left corner of the radar side
        if rv_frame_ts:
            try:
                from datetime import timezone as _tz_mod2
                _ts_label = _dt.fromtimestamp(int(rv_frame_ts), tz=_tz_mod2.utc).astimezone().strftime("%-I:%M %p")
                _ts_font = hdr_font or ImageFont.load_default()
                _ts_bb = draw_tmp.textbbox((0, 0), _ts_label, font=_ts_font)
                _ts_h = _ts_bb[3] - _ts_bb[1]
                draw_tmp.text((6, height - _ts_h - 4), _ts_label, fill=(255, 255, 255), font=_ts_font)
            except Exception:
                pass

        # Severe-weather warning polygons over the radar side (RainViewer only —
        # projection is only known for the tile source).
        if config.get("radar_alerts_overlay", False) and radar_source == "rainviewer":
            _overlay_severe_alerts(final_img, rv_lat, rv_lon, rv_zoom, radar_w, height, headers)

        primary_region = (0, 0, radar_w, height)
        processed_radar = None
    elif radar_mode == "seven_color":
        # Full-screen 7-color radar: uses all Waveshare e-ink ink channels.
        # Pixels are classified by precipitation intensity via HSV hue, then
        # remapped to the 7 display colors. A legend strip at the bottom labels
        # each color with its approximate dBZ tier.
        forecast_loc = config.get("forecast_location", {})
        rv_lat = config.get("rainviewer_lat") or forecast_loc.get("latitude")
        rv_lon = config.get("rainviewer_lon") or forecast_loc.get("longitude")
        if not rv_lat or not rv_lon:
            logger.error("seven_color mode requires lat/lon — set forecast_location or rainviewer_lat/lon")
            return None, False, None
        rv_zoom = config.get("rainviewer_zoom", 8)
        rv_color = config.get("rainviewer_color_scheme", 4)
        radar_h = height - _LEGEND_H
        radar_img_7c, frame_ts = _fetch_rainviewer_image(
            rv_lat, rv_lon, rv_zoom, width, radar_h, rv_color, headers
        )
        if radar_img_7c is None:
            logger.error("RainViewer fetch failed for seven_color mode")
            return None, False, None
        if frame_ts:
            _rv_state_path2 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "radar", "rv_frame_state.json")
            try:
                with open(_rv_state_path2, "w") as _f2:
                    import json as _json2
                    _json2.dump({"frame_ts": frame_ts}, _f2)
            except OSError:
                pass
        remapped = _remap_radar_seven_color(radar_img_7c)
        final_img.paste(remapped, (0, 0))
        # Severe-weather warning polygons (drawn after remap so pure red survives)
        if config.get("radar_alerts_overlay", False):
            _overlay_severe_alerts(final_img, rv_lat, rv_lon, rv_zoom, width, radar_h, headers)
        _draw_seven_color_legend(final_img, frame_ts, station, config)
        primary_region = (0, 0, width, radar_h)
        processed_radar = None
    else:
        raise ValueError(f"Invalid radar_mode '{radar_mode}'. Use 'crop', 'fit', 'panel', or 'seven_color'.")

    if processed_radar is not None:
        final_img.paste(processed_radar, (0, 0))

    old_quant = None
    if os.path.exists(quantized_output_path):
        old_quant = Image.open(quantized_output_path).convert("RGB")

    # In panel mode the conditions panel has its own QR; top-right QR is for crop/fit only.
    if config.get("check_special_weather", True) and special_msg and radar_mode != "panel":
        try:
            special_url = config.get('special_url', "https://forecast.weather.gov/showsigwx.php?warnzone=TNZ027&warncounty=TNC037&firewxzone=TNZ027&local_place1=Nashville%20TN")
            qr_topright = qrcode.make(special_url).convert("RGB").resize((138, 138), Image.LANCZOS)
            _banner_h = config.get("alert_banner_height", 40)
            final_img.paste(qr_topright, (final_img.width - qr_topright.width - 2, _banner_h + 2))
        except Exception as e:
            logger.error("Error adding special weather QR code: %s", e)

    draw = ImageDraw.Draw(final_img)

    # --- Alert Banner ---
    if config.get("check_special_weather", True) and special_msg:
        alert_banner_height = config.get("alert_banner_height", 40)
        headline = get_alert_headline(special_msg)
        if headline:
            # In panel mode restrict the banner to the radar side so it doesn't overwrite the
            # conditions panel QR code (the panel header already turns red for alerts).
            _banner_right = (width - config.get("panel_width", 280) - 1) if radar_mode == "panel" else (width - 1)
            draw.rectangle([(0, 0), (_banner_right, alert_banner_height - 1)], fill=(0, 0, 0))
            font_path = config.get("font_path", "")
            try:
                banner_font = ImageFont.truetype(font_path, 20)
            except Exception:
                banner_font = ImageFont.load_default()
            bbox = draw.textbbox((0, 0), headline, font=banner_font)
            text_h = bbox[3] - bbox[1]
            text_y = (alert_banner_height - text_h) // 2
            draw.text((8, text_y), headline, fill=(255, 255, 255), font=banner_font)
    # --- End Alert Banner ---

    final_img.save(output_path)
    logger.info("Saved final weather image to %s", output_path)

    more_colors = config.get('more_colors', False)
    quantize_to_seven_colors(output_path, quantized_output_path, more_colors, threshold=75)
    new_quant = Image.open(quantized_output_path).convert("RGB")
    if old_quant is not None and images_are_equal(old_quant, new_quant):
        logger.info("Station %s: Quantized image unchanged.", station)
        return None, False, primary_region
    return quantized_output_path, True, primary_region


def calculate_non_bw_percentage(image_path, region=None):
    image = Image.open(image_path).convert("RGB")
    if region:
        image = image.crop(region)
    pixels = list(image.getdata())
    if not pixels:
        return 0.0
    non_bw_count = sum(1 for pixel in pixels if pixel not in [(0, 0, 0), (255, 255, 255)])
    return (non_bw_count / len(pixels)) * 100


def full_station_scan(config, skip_station=None):
    if config.get("radar_source", "ridge").lower() == "rainviewer":
        # RainViewer is a CONUS composite — per-station scanning doesn't apply
        return {}

    # Force "fit" mode during scans — no panel/API calls per station
    saved_mode = config.get("radar_mode")
    config["radar_mode"] = "fit"
    percentages = {}
    special_msg = get_special_weather_messages()
    for station_data in config.get("stations", []):
        station = station_data.get("name")
        if not station or station == skip_station:
            continue
        config["station"] = station_data
        config["output_path"] = os.path.join("radar", f"eink_display_{station}.bmp")
        config["quantized_path"] = os.path.join("radar", f"eink_quantized_display_{station}.bmp")
        path, updated, _ = generate_weather_image(config, special_msg=special_msg)
        if path is None and not updated:
            continue
        perc = calculate_non_bw_percentage(config["quantized_path"])
        percentages[station] = perc
        logger.info("Full scan: Station %s -> %.2f%%", station, perc)
    config["radar_mode"] = saved_mode
    return percentages


def update_top5(percentages):
    sorted_stations = sorted(percentages.items(), key=lambda x: x[1], reverse=True)
    top5 = sorted_stations[:5]
    logger.info("Top 5 stations from full scan: %s", top5)
    return top5


def generate(config):
    """Module interface: run the weather radar pipeline and return final display image path."""
    radar_folder = "radar"
    os.makedirs(radar_folder, exist_ok=True)

    special_msg = get_special_weather_messages()
    state = load_state(STATE_FILE) or {}
    now = time.time()
    full_scan_interval = config.get('full_scan_interval', 3600)

    top5_data = state.get("top5", [])
    if top5_data:
        top5_list = [(item["station"], item["percentage"]) for item in top5_data]
        config["top5"] = top5_list
    else:
        top5_list = []

    default_station = config.get("station", {}).get("name", "KTYX")
    config["output_path"] = os.path.join(radar_folder, f"eink_display_{default_station}.bmp")
    config["quantized_path"] = os.path.join(radar_folder, f"eink_quantized_display_{default_station}.bmp")
    default_image_path, default_updated, default_region = generate_weather_image(config, special_msg=special_msg)
    if default_image_path is None and not default_updated:
        default_image_path = config["quantized_path"]
    default_percentage = calculate_non_bw_percentage(config["quantized_path"], region=default_region)
    logger.info("Default station (%s) has %.2f%% interesting pixels.", default_station, default_percentage)
    should_update = default_updated
    final_display_image = default_image_path

    save_state(STATE_FILE, state)

    best_station = None
    best_percentage = default_percentage

    final_percentage = best_percentage if best_station else default_percentage

    if config.get('show_forecast_fallback', False) and final_percentage < config.get('interesting_threshold', 15):
        logger.info("No interesting radar found (best: %.2f%%). Falling back to forecast.", final_percentage)
        forecast_config = config.get('forecast_location', {})
        lat = forecast_config.get('latitude')
        lon = forecast_config.get('longitude')
        if lat and lon:
            forecast_data = get_detailed_forecast(lat, lon)
            if forecast_data:
                forecast_output = os.path.join(radar_folder, "forecast_display.bmp")
                forecast_path = generate_forecast_image(config, forecast_data, forecast_output)
                if forecast_path:
                    final_display_image = forecast_path
                    should_update = True

    current_station = best_station if best_station else default_station
    last_ten = state.get("last_ten", [])
    last_ten.append(current_station)
    if len(last_ten) > 10:
        last_ten = last_ten[-10:]
    state["last_ten"] = last_ten
    config["last_ten"] = last_ten
    save_state(STATE_FILE, state)

    return final_display_image if should_update else None


def main():
    config = load_config('config.yml')
    output = generate(config)
    if output and platform.system() == "Linux":
        display_color_image(output)
    elif output:
        logger.info("Generated: %s (display skipped on macOS)", output)
    else:
        logger.info("No image changes detected.")


if __name__ == '__main__':
    main()
