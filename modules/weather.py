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
from modules.river_height import _get_river_data, _resolve_thresholds, _stage_color_and_label
from modules.air_quality import _get_aqi_data, _aqi_color, _aqi_category
from datetime import datetime as _dt, date as _date, timedelta as _td
from zoneinfo import ZoneInfo
from astral import LocationInfo
from astral.sun import sun as _astral_sun
from concurrent.futures import ThreadPoolExecutor
import hashlib
import math
import platform
import os

from utils import get_font, get_logger

logger = get_logger("weather")

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

        # Precip type (rain/snow/mix) — radar reflectivity alone can't distinguish these,
        # so estimate from the freezing level height above ground during active precip.
        freezing_hourly = hourly.get("freezing_level_height", [])
        elevation_m = data.get("elevation", 0) or 0
        precip_type = None
        if (50 <= weather_code < 100 and cur_idx >= 0 and freezing_hourly
                and cur_idx < len(freezing_hourly) and freezing_hourly[cur_idx] is not None):
            agl = freezing_hourly[cur_idx] - elevation_m
            if agl < 300:
                precip_type = "Snow"
            elif agl < 1000:
                precip_type = "Wintry Mix"

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
            "precip_type":  precip_type,
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
        f"&hourly=visibility,surface_pressure,relative_humidity_2m,temperature_2m,weather_code,precipitation_probability,uv_index,freezing_level_height"
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
    if conditions.get("precip_type"):
        rows.append(("Precip Type", conditions["precip_type"], ""))

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


_BADGE_H = 20
# Exact RGB values river_height.py/air_quality.py already use for their
# "attention" severity tiers — dark enough that badge text needs to be white,
# not black, to stay readable. (Their lighter tiers — yellow, green, blue — use
# black text, which is also this function's default.)
_BADGE_WHITE_TEXT_BGS = {(255, 128, 0), (220, 0, 0), (255, 0, 0), (150, 0, 150), (0, 0, 0)}


def _draw_status_badges(canvas, config, panel_x, panel_w, start_y) -> int:
    """
    Draw at most one merged colored status badge — river flood stage and/or air
    quality — below the panel content. Call AFTER the panel's B/W snap so the
    badge's severity color survives the final quantize pass. Cross-links the
    already-configured modules.river_height / modules.air_quality data sources;
    each badge is independently gated on its own module having usable data.

    Returns the y position after the badge (== start_y if nothing triggered).
    """
    height = canvas.size[1]
    # Hard budget guard: never let a badge squeeze the always-present hourly
    # forecast strip below a usable height. Badges are rare-event extras — if
    # there isn't enough headroom left, they simply don't render.
    if start_y > height - 110:
        return start_y

    draw = ImageDraw.Draw(canvas)
    margin = 8
    text_x = panel_x + margin
    right_x = panel_x + panel_w - margin
    max_w = right_x - text_x - 6
    font = get_font(13, bold=True, config=config)

    parts = []  # (text, color), river first then AQI

    river_cfg = config.get("river_height", {}) or {}
    site = str(river_cfg.get("site_number", "")).strip()
    if site:
        try:
            river_data = _get_river_data(river_cfg, river_cfg.get("cache_dir", "data/"))
            if river_data:
                thresholds = _resolve_thresholds(river_cfg)
                color, label = _stage_color_and_label(river_data["current_ft"], thresholds)
                if label != "Normal":
                    parts.append((f"River {river_data['current_ft']:.1f}ft {label}", color))
        except Exception as e:
            logger.warning("River badge error: %s", e)

    aqi_cfg = config.get("air_quality", {}) or {}
    api_key = aqi_cfg.get("api_key", "")
    if api_key:
        try:
            aqi_data = _get_aqi_data(aqi_cfg.get("zip_code", ""), api_key, aqi_cfg.get("cache_dir", "data/"))
            if aqi_data and aqi_data.get("aqi", 0) > 50:
                cat = aqi_data.get("category") or _aqi_category(aqi_data["aqi"])
                parts.append((f"AQI {aqi_data['aqi']} {cat}", _aqi_color(aqi_data["aqi"])))
        except Exception as e:
            logger.warning("AQI badge error: %s", e)

    if not parts:
        return start_y

    text = "  |  ".join(t for t, _ in parts)
    color = tuple(parts[0][1])  # single-color background even when merged; first badge wins
    while text and draw.textbbox((0, 0), text, font=font)[2] > max_w:
        text = text[:-1]

    y = start_y
    draw.rectangle([(text_x, y), (right_x, y + _BADGE_H)], fill=color)
    tcolor = (255, 255, 255) if color in _BADGE_WHITE_TEXT_BGS else (0, 0, 0)
    tb = draw.textbbox((0, 0), text, font=font)
    text_pos = (text_x + 4, y + (_BADGE_H - (tb[3] - tb[1])) // 2 - tb[1])
    draw.text(text_pos, text, fill=tcolor, font=font)
    _snap_region_2color(
        canvas,
        (text_pos[0] + tb[0], text_pos[1] + tb[1], text_pos[0] + tb[2] + 1, text_pos[1] + tb[3] + 1),
        color, tcolor,
    )
    return y + _BADGE_H + 3


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
    return np.array_equal(np.asarray(img1), np.asarray(img2))


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
    arr = np.asarray(original, dtype=np.int32)
    pal = np.asarray(palette_5, dtype=np.int32)

    # Nearest palette color per pixel. Accumulating the running minimum one
    # palette entry at a time keeps peak memory at a couple of H×W arrays
    # instead of the H×W×P array a fully broadcast distance matrix would need.
    # Strict `<` preserves the first-wins tie-breaking of the original min().
    best_d2 = np.full(arr.shape[:2], np.iinfo(np.int32).max, dtype=np.int32)
    best_idx = np.zeros(arr.shape[:2], dtype=np.intp)
    for i, color in enumerate(pal):
        d2 = ((arr - color) ** 2).sum(axis=-1)
        closer = d2 < best_d2
        best_d2 = np.where(closer, d2, best_d2)
        best_idx = np.where(closer, i, best_idx)
    out = pal[best_idx]

    # Near-white pixels snap to white regardless of the palette (white is not a
    # palette entry, so without this they would land on an arbitrary ink).
    d_white2 = ((arr - np.asarray(white, dtype=np.int32)) ** 2).sum(axis=-1)
    out = np.where((d_white2 <= threshold * threshold)[..., None], np.int32(255), out)

    Image.fromarray(out.astype(np.uint8), "RGB").save(output_path, format="bmp")
    logger.info("Quantized image saved to %s", output_path)


_RV_TILE_SIZE  = 512

# On-disk radar tile cache. RainViewer frame paths embed the frame timestamp, so
# a cached tile is immutable for its key and never needs a TTL — the previous
# frame fetched for storm motion was the current frame one cycle ago, so it is
# already on disk. Bounded by count and pruned oldest-first.
_TILE_CACHE_DIR = os.path.join("data", "rv_tiles")
_TILE_CACHE_MAX = 240
_TILE_WORKERS   = 4


def _merc_xy(lat: float, lon: float, scale: float) -> Tuple[float, float]:
    """Web-Mercator world pixel coordinates, where `scale` = 2**zoom * tile_size."""
    x = (lon + 180.0) / 360.0 * scale
    lat_rad = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * scale
    return x, y


def _merc_latlon(x: float, y: float, scale: float) -> Tuple[float, float]:
    """Inverse of `_merc_xy`: world pixel coordinates back to (lat, lon)."""
    lon = x / scale * 360.0 - 180.0
    n = math.pi - 2.0 * math.pi * y / scale
    lat = math.degrees(math.atan(math.sinh(n)))
    return lat, lon


def _view_bounds(
    lat: float, lon: float, zoom: int, width: int, height: int,
    tile_size: int = _RV_TILE_SIZE,
) -> Tuple[float, float, float, float]:
    """(lat_min, lon_min, lat_max, lon_max) covered by a width×height view."""
    scale = (2 ** zoom) * tile_size
    cx, cy = _merc_xy(lat, lon, scale)
    lat_max, lon_min = _merc_latlon(cx - width / 2.0, cy - height / 2.0, scale)
    lat_min, lon_max = _merc_latlon(cx + width / 2.0, cy + height / 2.0, scale)
    return lat_min, lon_min, lat_max, lon_max


def _rv_tile_geometry(
    lat: float, lon: float, zoom: int, width: int, height: int, tile_size: int
) -> Tuple[int, int, int, int, int, int]:
    """Return (tx_min, ty_min, tx_max, ty_max, crop_x, crop_y) for a lat/lon-centered view."""
    n = 2 ** zoom
    cx_px = (lon + 180.0) / 360.0 * n * tile_size
    lat_rad = math.radians(lat)
    cy_px = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n * tile_size
    left_px = cx_px - width / 2.0
    top_px  = cy_px - height / 2.0
    tx_min = int(left_px // tile_size)
    ty_min = int(top_px  // tile_size)
    tx_max = int((left_px + width  - 1) // tile_size)
    ty_max = int((top_px  + height - 1) // tile_size)
    crop_x = int(left_px - tx_min * tile_size)
    crop_y = int(top_px  - ty_min * tile_size)
    return tx_min, ty_min, tx_max, ty_max, crop_x, crop_y


def _tile_cache_path(
    path: str, tile_size: int, zoom: int, tx: int, ty: int, color_scheme: int
) -> str:
    key = f"{path}|{tile_size}|{zoom}|{tx}|{ty}|{color_scheme}"
    return os.path.join(_TILE_CACHE_DIR, hashlib.sha1(key.encode()).hexdigest()[:20] + ".png")


def _prune_tile_cache(max_files: int = _TILE_CACHE_MAX) -> None:
    """Keep the tile cache bounded, dropping the least recently written first."""
    try:
        entries = [os.path.join(_TILE_CACHE_DIR, f) for f in os.listdir(_TILE_CACHE_DIR)]
    except OSError:
        return
    if len(entries) <= max_files:
        return
    try:
        entries.sort(key=os.path.getmtime)
        for stale in entries[:len(entries) - max_files]:
            os.remove(stale)
    except OSError:
        pass


def _fetch_rv_frame(
    path: str, tx_min: int, ty_min: int, tx_max: int, ty_max: int,
    zoom: int, tile_size: int, color_scheme: int, headers: dict,
    crop_x: int, crop_y: int, width: int, height: int,
    use_cache: bool = True,
) -> Image.Image:
    """Fetch RainViewer radar tiles for one frame, stitch, and crop to display size (RGBA).

    Tiles are fetched in parallel and cached on disk — a frame needs up to 4 tiles
    and a render needs several frames, which is a long serial stall on a Pi.
    """
    cw = (tx_max - tx_min + 1) * tile_size
    ch = (ty_max - ty_min + 1) * tile_size
    canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    coords = [(tx, ty) for tx in range(tx_min, tx_max + 1) for ty in range(ty_min, ty_max + 1)]

    def _one(coord):
        tx, ty = coord
        cache_file = _tile_cache_path(path, tile_size, zoom, tx, ty, color_scheme)
        if use_cache and os.path.exists(cache_file):
            try:
                with Image.open(cache_file) as cached:
                    return coord, cached.convert("RGBA")
            except Exception:
                pass  # corrupt cache entry — refetch below
        url = (f"https://tilecache.rainviewer.com{path}"
               f"/{tile_size}/{zoom}/{tx}/{ty}/{color_scheme}/1_1.png")
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code != 200:
                logger.warning("RainViewer tile %d/%d HTTP %d", tx, ty, resp.status_code)
                return coord, None
            tile = Image.open(io.BytesIO(resp.content)).convert("RGBA")
            if use_cache:
                try:
                    os.makedirs(_TILE_CACHE_DIR, exist_ok=True)
                    with open(cache_file, "wb") as fh:
                        fh.write(resp.content)
                except OSError:
                    pass
            return coord, tile
        except Exception as e:
            logger.warning("RainViewer tile %d/%d failed: %s", tx, ty, e)
            return coord, None

    if coords:
        with ThreadPoolExecutor(max_workers=min(_TILE_WORKERS, len(coords))) as pool:
            results = list(pool.map(_one, coords))
        for (tx, ty), tile in results:
            if tile is not None:
                canvas.paste(tile, ((tx - tx_min) * tile_size, (ty - ty_min) * tile_size), tile)
        _prune_tile_cache()

    return canvas.crop((crop_x, crop_y, crop_x + width, crop_y + height))


def _fetch_osm_roads(
    lat: float, lon: float, zoom: int, tile_size: int, width: int, height: int,
) -> Image.Image:
    """Render interstate and trunk highways from OSM Overpass onto a transparent layer.

    Uses actual road geometry (not raster tiles), so lines are crisp and correctly
    sized at any zoom level.  Results are cached for 24 hours to avoid re-fetching.
    Falls back to a fully transparent layer on network failure.

    Each road is drawn as a black core inside a white casing. Without the casing a
    road is indistinguishable from the black ">60 dBZ" reflectivity tier once the
    image is quantized to the 7-color palette — no precipitation core is ever
    outlined in white, so the casing is what makes a road read as a road.
    """
    import json, time

    scale = 2 ** zoom * tile_size

    def _lpx(la: float, lo: float) -> Tuple[float, float]:
        return _merc_xy(la, lo, scale)

    cx, cy = _lpx(lat, lon)
    x0, y0 = cx - width / 2, cy - height / 2

    lat_min, lon_min, lat_max, lon_max = _view_bounds(lat, lon, zoom, width, height, tile_size)

    cache_path = os.path.join("data", "osm_roads_cache.json")
    # The Overpass bbox depends on the viewport, not just the centre — a cache key
    # without width/height silently reuses a narrower way set when the radar area
    # changes size (e.g. switching radar_mode panel <-> seven_color).
    cache_key  = f"{lat:.4f},{lon:.4f},{zoom},{width}x{height}"
    ways: Optional[list] = None

    try:
        if os.path.exists(cache_path):
            with open(cache_path) as _f:
                _c = json.load(_f)
            if _c.get("key") == cache_key and time.time() - _c.get("ts", 0) < 86400:
                ways = _c["ways"]
                logger.info("OSM roads: %d ways from cache", len(ways))
    except Exception:
        pass

    if ways is None:
        query = (
            f"[out:json][timeout:30];"
            f"way[highway~\"^(motorway|trunk)$\"]"
            f"({lat_min:.4f},{lon_min:.4f},{lat_max:.4f},{lon_max:.4f});"
            f"out geom;"
        )
        try:
            resp = requests.get(
                "https://overpass-api.de/api/interpreter",
                params={"data": query},
                headers={"User-Agent": "eink-display/1.0"},
                timeout=45,
            )
            resp.raise_for_status()
            data = resp.json()
            ways = [e for e in data.get("elements", []) if e.get("type") == "way"]
            logger.info("OSM roads: fetched %d ways from Overpass", len(ways))
            os.makedirs("data", exist_ok=True)
            with open(cache_path, "w") as _f:
                json.dump({"key": cache_key, "ts": time.time(), "ways": ways}, _f)
        except Exception as e:
            logger.warning("OSM road fetch failed: %s", e)
            return Image.new("RGBA", (width, height), (0, 0, 0, 0))

    img  = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    polylines = []
    for way in ways:
        if "geometry" not in way:
            continue
        pts = []
        for node in way["geometry"]:
            wx, wy = _lpx(node["lat"], node["lon"])
            pts.append((wx - x0, wy - y0))
        if len(pts) >= 2:
            hw = way.get("tags", {}).get("highway", "")
            polylines.append((pts, 3 if hw == "motorway" else 2))

    # Casing first (all roads), then cores — otherwise a later road's casing
    # would erase an earlier road's core at every junction.
    for pts, lw in polylines:
        draw.line(pts, fill=(255, 255, 255, 255), width=lw + 4)
    for pts, lw in polylines:
        draw.line(pts, fill=(0, 0, 0, 255), width=lw)
    return img


# Peak must exceed the correlation surface's mean by this many standard
# deviations to count as a real match. A phase-correlation surface has a mean of
# essentially zero (it is the DC term spread over rH*rW pixels), so the old
# `peak < mean * 3` test was a no-op — uncorrelated noise patches passed it 100%
# of the time and fed junk vectors into the median.
#
# Calibrated by measurement: on pairs of independent 5%-density noise frames,
# 4 sigma still let 11-21 spurious vectors through per pair, while 6 sigma let
# none. A genuine 10px translation yields all 20 of its vectors and the exact
# right answer anywhere from 5 through 12 sigma, with or without 5% background
# noise, so 6 sits comfortably inside that plateau rather than on its edge.
_PEAK_SIGMA = 6.0

# Grouping tracked patches into storm cells. The patch grid steps by
# CELL_SZ//2 = 20 px, so the radius links each patch to its immediate and
# next-nearest grid neighbours. Proximity alone is not enough: two cells whose
# precipitation shields touch would chain into one blob, which is precisely the
# splitting-supercell case per-cell arrows exist to resolve. Patches must also
# agree on velocity to within _CLUSTER_VEL_TOL px/frame (~18 km/h at zoom 7 on a
# 10-minute frame interval).
_CLUSTER_RADIUS  = 45.0
_CLUSTER_VEL_TOL = 6.0


def _patch_motion_vectors(
    prev_rgba: Image.Image, curr_rgba: Image.Image
) -> List[Tuple[float, float, float, float]]:
    """
    Track individual precipitation patches between two radar frames.

    Divides the image into overlapping 40×40 px patches and, for each patch with
    meaningful precipitation in the current frame, searches the previous frame
    for the best-matching position via phase cross-correlation.

    Returns a list of (cx, cy, dx, dy): the patch centre in current-frame pixels
    and its displacement, where positive dx = eastward and negative dy =
    northward (i.e. forward travel direction, not origin). Returns [] if the
    frames hold too little precipitation to track.
    """
    prev_a = np.array(prev_rgba.getchannel("A"), dtype=np.float32)
    curr_a = np.array(curr_rgba.getchannel("A"), dtype=np.float32)
    if prev_a.sum() < 500 or curr_a.sum() < 500:
        return []

    H, W = curr_a.shape
    CELL_SZ  = 40
    SEARCH_R = 25
    MIN_SUM  = 1500  # alpha sum threshold for a patch to be tracked

    vectors: List[Tuple[float, float, float, float]] = []

    for gy in range(0, H - CELL_SZ + 1, CELL_SZ // 2):
        for gx in range(0, W - CELL_SZ + 1, CELL_SZ // 2):
            curr_patch = curr_a[gy:gy + CELL_SZ, gx:gx + CELL_SZ]
            if curr_patch.sum() < MIN_SUM:
                continue

            py1 = max(0, gy - SEARCH_R)
            py2 = min(H, gy + CELL_SZ + SEARCH_R)
            px1 = max(0, gx - SEARCH_R)
            px2 = min(W, gx + CELL_SZ + SEARCH_R)
            prev_region = prev_a[py1:py2, px1:px2]
            rH, rW = prev_region.shape

            if rH < CELL_SZ or rW < CELL_SZ:
                continue

            # Phase cross-correlation: F_prev * conj(F_curr_padded).
            # Peak at (peak_y, peak_x) means the curr patch best matches
            # the prev region starting at that offset.
            curr_pad = np.zeros((rH, rW), dtype=np.float32)
            curr_pad[:CELL_SZ, :CELL_SZ] = curr_patch
            F_prev = np.fft.rfft2(prev_region)
            F_curr = np.fft.rfft2(curr_pad)
            cross = F_prev * np.conj(F_curr)
            nrm = np.abs(cross); nrm[nrm == 0] = 1
            corr = np.real(np.fft.irfft2(cross / nrm, s=(rH, rW)))

            peak_y, peak_x = np.unravel_index(np.argmax(corr), corr.shape)
            corr_std = corr.std()
            if corr_std <= 0:
                continue
            if corr[peak_y, peak_x] < corr.mean() + _PEAK_SIGMA * corr_std:
                continue  # weak peak → skip

            # Unwrap circular shift: treat peaks near the right/bottom edge as
            # large negative shifts (the correlation wrapped around).
            if peak_y > rH // 2:
                peak_y -= rH
            if peak_x > rW // 2:
                peak_x -= rW

            dy_vec = gy - (py1 + peak_y)
            dx_vec = gx - (px1 + peak_x)

            # Discard vectors that hit the search boundary (likely under-estimated).
            if abs(dy_vec) > SEARCH_R - 2 or abs(dx_vec) > SEARCH_R - 2:
                continue

            vectors.append((gx + CELL_SZ / 2.0, gy + CELL_SZ / 2.0,
                            float(dx_vec), float(dy_vec)))

    return vectors


def _compute_storm_motion(
    prev_rgba: Image.Image, curr_rgba: Image.Image
) -> Optional[Tuple[float, float]]:
    """
    Estimate bulk storm motion (dx, dy) in pixels via patch cross-correlation.

    The median displacement across all tracked patches, where positive dx =
    eastward and negative dy = northward (forward travel direction, not origin).

    Patch tracking correctly handles MCS systems where the bulk-precipitation
    centroid drifts opposite to individual cell motion (new cells fire on the
    trailing side while existing cells translate in the actual travel direction).
    """
    try:
        vectors = _patch_motion_vectors(prev_rgba, curr_rgba)
        if len(vectors) < 3:
            return None

        vx = float(np.median([v[2] for v in vectors]))
        vy = float(np.median([v[3] for v in vectors]))

        if math.sqrt(vx ** 2 + vy ** 2) < 1.0:
            return None

        return (vx, vy)
    except Exception as e:
        logger.warning("Storm motion computation failed: %s", e)
        return None


def _cluster_motion_vectors(
    vectors: List[Tuple[float, float, float, float]],
    max_clusters: int = 5,
    min_members: int = 3,
) -> List[Tuple[float, float, float, float]]:
    """
    Group per-patch motion vectors into spatially coherent cells.

    Patches are clustered by proximity (single-link, `_CLUSTER_RADIUS` px) so a
    bow echo or a splitting supercell yields one arrow per limb instead of a
    single averaged direction that is wrong for both. Each cluster is reduced to
    (cx, cy, dx, dy) using the member centroid and median displacement — median
    so one mistracked patch cannot swing the arrow.

    Returns the largest `max_clusters` clusters, biggest first. Clusters with
    fewer than `min_members` patches are dropped as unreliable.
    """
    if not vectors:
        return []

    pts = np.array([(v[0], v[1]) for v in vectors], dtype=np.float32)
    vel = np.array([(v[2], v[3]) for v in vectors], dtype=np.float32)
    n = len(vectors)
    taken = np.zeros(n, dtype=bool)
    clusters: List[List[int]] = []
    r2 = _CLUSTER_RADIUS ** 2
    v2 = _CLUSTER_VEL_TOL ** 2

    for seed in range(n):
        if taken[seed]:
            continue
        taken[seed] = True
        members = [seed]
        frontier = [seed]
        while frontier:
            i = frontier.pop()
            close = ((pts - pts[i]) ** 2).sum(axis=1) <= r2
            alike = ((vel - vel[i]) ** 2).sum(axis=1) <= v2
            near = np.flatnonzero(close & alike & ~taken)
            taken[near] = True
            members.extend(near.tolist())
            frontier.extend(near.tolist())
        clusters.append(members)

    out = []
    for members in sorted(clusters, key=len, reverse=True):
        if len(members) < min_members:
            continue
        cx = float(np.mean([vectors[i][0] for i in members]))
        cy = float(np.mean([vectors[i][1] for i in members]))
        dx = float(np.median([vectors[i][2] for i in members]))
        dy = float(np.median([vectors[i][3] for i in members]))
        if math.hypot(dx, dy) < 1.0:
            continue  # stationary cluster — no meaningful direction to draw
        out.append((cx, cy, dx, dy))
        if len(out) >= max_clusters:
            break
    return out


def _draw_emoji_arrow(
    draw: ImageDraw.ImageDraw,
    cx: float, cy: float,
    ux: float, uy: float,
    color,
    size: int = 18,
) -> None:
    """Draw a ➡️-style arrow centered at (cx, cy) pointing in unit direction (ux, uy).
    Has a narrow rectangular shaft (45% of length) and a wider filled triangle head (55%)."""
    px, py = -uy, ux  # perpendicular unit vector

    half      = size / 2.0
    shaft_len = size * 0.45
    shaft_hw  = size * 0.12
    head_hw   = size * 0.40

    # Tail (base)
    bx, by = cx - ux * half, cy - uy * half
    # Shaft/head junction
    jx, jy = bx + ux * shaft_len, by + uy * shaft_len
    # Tip
    tx, ty = bx + ux * size, by + uy * size

    shaft = [
        (bx + px * shaft_hw, by + py * shaft_hw),
        (bx - px * shaft_hw, by - py * shaft_hw),
        (jx - px * shaft_hw, jy - py * shaft_hw),
        (jx + px * shaft_hw, jy + py * shaft_hw),
    ]
    head = [
        (jx + px * head_hw, jy + py * head_hw),
        (jx - px * head_hw, jy - py * head_hw),
        (tx, ty),
    ]
    draw.polygon(shaft, fill=color)
    draw.polygon(head, fill=color)


def _bearing_from_vector(ux: float, uy: float) -> float:
    """Compass bearing (degrees, 0=N) that screen vector (ux, uy) travels toward.

    Screen y grows downward, so north is -y. This is the direction of travel,
    not the meteorological "from" convention used for wind.
    """
    return math.degrees(math.atan2(ux, -uy)) % 360.0


def _storm_speed_kmh(mag_px: float, km_per_px: float, interval_min: float) -> float:
    """Convert a per-frame pixel displacement to ground speed in km/h."""
    if interval_min <= 0 or km_per_px <= 0:
        return 0.0
    return mag_px * km_per_px / (interval_min / 60.0)


def _precip_arrival_minutes(
    precip_mask: Image.Image,
    ux: float, uy: float,
    speed_kmh: float,
    km_per_px: float,
    home_xy: Tuple[float, float],
    corridor_px: float = 40.0,
) -> Optional[float]:
    """
    Minutes until the nearest upwind precipitation reaches home, by extrapolation.

    Transforms every precipitation pixel into (along, cross) coordinates relative
    to home, where `along` runs along the direction of travel. Pixels with
    `along < 0` are upwind (they have yet to pass home); those within
    `corridor_px` of the motion axis are on track to hit it. The closest such
    pixel sets the arrival time.

    Returns 0.0 if it is already precipitating at home, or None if nothing is
    heading that way. This is a straight-line extrapolation of current motion —
    `_nowcast_arrival_minutes` is preferred when the nowcast frames are available,
    since those account for growth and decay.
    """
    if speed_kmh <= 0 or km_per_px <= 0:
        return None

    alpha = np.array(precip_mask.getchannel("A"))
    ys, xs = np.where(alpha > 30)
    if len(xs) == 0:
        return None

    hx, hy = home_xy
    H, W = alpha.shape
    # Already raining here? Check a small neighbourhood around home.
    y0, y1 = max(0, int(hy) - 3), min(H, int(hy) + 4)
    x0, x1 = max(0, int(hx) - 3), min(W, int(hx) + 4)
    if y1 > y0 and x1 > x0 and (alpha[y0:y1, x0:x1] > 30).any():
        return 0.0

    rx = xs.astype(np.float32) - hx
    ry = ys.astype(np.float32) - hy
    along = rx * ux + ry * uy
    cross = rx * (-uy) + ry * ux

    upwind = (along < 0) & (np.abs(cross) <= corridor_px)
    if not upwind.any():
        return None

    dist_px = float(-along[upwind].max())  # nearest upwind pixel
    if dist_px <= 0:
        return None
    return dist_px * km_per_px / speed_kmh * 60.0


def _draw_storm_motion_overlay(
    img: Image.Image,
    motion: Tuple[float, float],
    precip_mask: Image.Image,
    config: dict,
    km_per_px: float,
    frame_interval_min: float,
    vectors: Optional[List[Tuple[float, float, float, float]]] = None,
) -> Optional[List[str]]:
    """Overlay storm motion arrows on img (in-place) and return label lines.

    When per-patch `vectors` are supplied, they are clustered into storm cells and
    each cell gets an arrow pointing in *its own* direction — a splitting
    supercell or a bow echo renders as diverging arrows rather than one averaged
    heading that is wrong for every limb. Without them (or when clustering finds
    nothing reliable), it falls back to placing arrows for the bulk direction at
    the leading edge of each cross-track slice of precipitation.

    Returns the caption lines for the bottom-left stack (speed/direction, and an
    extrapolated arrival estimate), or None if motion is too weak to report.
    """
    dx, dy = motion
    mag = math.sqrt(dx ** 2 + dy ** 2)
    if mag < 1:
        return None

    # (dx, dy) is already the forward travel direction (prev→curr displacement).
    ux, uy = dx / mag, dy / mag
    draw = ImageDraw.Draw(img)
    W, H  = img.size

    CELL_ARROW_COLOR = (0, 0, 0)
    MAX_ARROWS       = 5
    ARROW_SIZE = 20
    MARGIN = ARROW_SIZE // 2  # gap between cell edge and arrow tail

    # (centre_x, centre_y, unit_x, unit_y) per arrow
    arrows: List[Tuple[float, float, float, float]] = []

    # Clusters come back largest-first, so clusters[0] is the dominant cell.
    clusters = _cluster_motion_vectors(vectors or [], max_clusters=MAX_ARROWS)
    for cx, cy, cdx, cdy in clusters:
        cmag = math.hypot(cdx, cdy)
        if cmag < 1:
            continue
        cux, cuy = cdx / cmag, cdy / cmag
        ax = float(np.clip(cx + cux * MARGIN, ARROW_SIZE, W - ARROW_SIZE))
        ay = float(np.clip(cy + cuy * MARGIN, ARROW_SIZE, H - ARROW_SIZE))
        arrows.append((ax, ay, cux, cuy))

    if not arrows:
        # --- fallback: bulk direction at the leading edge of each slice --------
        # "Leading edge" = the precipitation pixel with the highest projection onto
        # the motion direction, so the arrow sits just ahead of the cell rather than
        # covering it.
        alpha = np.array(precip_mask.getchannel("A"))
        ys, xs = np.where(alpha > 30)
        if len(ys) >= 20:
            pts = np.stack([xs, ys], axis=1).astype(np.float32)
            # Split into spatial buckets along the axis perpendicular to motion
            # (cross-track), so we sample distinct cells rather than all bunching up.
            px2, py2 = -uy, ux  # perpendicular to motion
            cross_proj = pts[:, 0] * px2 + pts[:, 1] * py2
            order  = np.argsort(cross_proj)
            pts    = pts[order]
            chunks = np.array_split(pts, min(MAX_ARROWS, len(pts)))
            SAFE = ARROW_SIZE + MARGIN  # clearance from image edge for a leading-edge arrow
            for chunk in chunks:
                if len(chunk) == 0:
                    continue
                # Prefer the leading-edge pixel that still has room ahead for an arrow.
                # Filter to pixels that keep the arrow fully within the image after MARGIN offset.
                inner = chunk[
                    (chunk[:, 0] >= SAFE) & (chunk[:, 0] <= W - SAFE) &
                    (chunk[:, 1] >= SAFE) & (chunk[:, 1] <= H - SAFE)
                ]
                if len(inner) > 0:
                    fwd_proj = inner[:, 0] * ux + inner[:, 1] * uy
                    lx, ly   = inner[int(np.argmax(fwd_proj))]
                    ax = lx + ux * MARGIN
                    ay = ly + uy * MARGIN
                else:
                    # No leading pixel far enough from the edge: fall back to centroid.
                    ax = float(chunk[:, 0].mean())
                    ay = float(chunk[:, 1].mean())
                ax = float(np.clip(ax, ARROW_SIZE, W - ARROW_SIZE))
                ay = float(np.clip(ay, ARROW_SIZE, H - ARROW_SIZE))
                arrows.append((ax, ay, ux, uy))

    for ax, ay, aux, auy in arrows:
        # White halo behind the arrow for visibility over any background
        _draw_emoji_arrow(draw, ax, ay, aux, auy, (255, 255, 255), size=ARROW_SIZE + 6)
        _draw_emoji_arrow(draw, ax, ay, aux, auy, CELL_ARROW_COLOR, size=ARROW_SIZE)

    # --- caption lines -------------------------------------------------------
    # Describe the dominant cell rather than the median across all of them: when
    # cells diverge, the median heading is one no individual storm is taking.
    if clusters:
        _, _, cdx, cdy = clusters[0]
        cap_mag = math.hypot(cdx, cdy)
        cap_ux, cap_uy = cdx / cap_mag, cdy / cap_mag
    else:
        cap_mag, cap_ux, cap_uy = mag, ux, uy

    speed_kmh = _storm_speed_kmh(cap_mag, km_per_px, frame_interval_min)
    if speed_kmh <= 0:
        return None

    use_metric = (config or {}).get("radar_speed_units", "mph").lower() in ("kmh", "km/h", "kph")
    speed_val = speed_kmh if use_metric else speed_kmh * 0.621371
    speed_unit = "km/h" if use_metric else "mph"
    heading = _deg_to_compass(_bearing_from_vector(cap_ux, cap_uy))

    label = "Storms" if len(clusters) > 1 else "Storm"
    lines = [f"{label} {heading} {speed_val:.0f} {speed_unit}"]

    eta = _precip_arrival_minutes(
        precip_mask, cap_ux, cap_uy, speed_kmh, km_per_px, (W / 2.0, H / 2.0)
    )
    if eta is not None and eta <= 180:
        lines.append("Rain here now" if eta < 2 else f"Rain here in ~{eta:.0f} min")

    return lines



# RainViewer publishes model-projected "nowcast" frames alongside the observed
# past frames, typically 3 at 10-minute steps. Capped so a render never fans out
# into an unbounded number of tile fetches.
_NOWCAST_MAX_FRAMES = 3


def _nowcast_arrival_minutes(
    frames: List[Dict[str, Any]],
    lat: float, lon: float, zoom: int, tile_size: int,
    color_scheme: int, headers: dict, now_ts: float,
) -> Optional[float]:
    """
    Minutes until precipitation reaches the map centre, per RainViewer's nowcast.

    Walks the nowcast frames in time order and returns the offset of the first one
    showing precipitation over home. Only a 32×32 px probe window around the centre
    is fetched (usually a single tile), so this costs one small request per frame
    rather than a full frame each.

    Preferred over `_precip_arrival_minutes`: the nowcast model accounts for growth
    and decay, whereas extrapolation assumes the current echo persists unchanged.
    """
    if not frames:
        return None

    probe = 32
    tx_min, ty_min, tx_max, ty_max, crop_x, crop_y = _rv_tile_geometry(
        lat, lon, zoom, probe, probe, tile_size
    )
    c = probe // 2
    for frame in frames:
        path = frame.get("path")
        ftime = frame.get("time")
        if not path or ftime is None:
            continue
        try:
            window = _fetch_rv_frame(path, tx_min, ty_min, tx_max, ty_max, zoom,
                                     tile_size, color_scheme, headers,
                                     crop_x, crop_y, probe, probe)
            alpha = np.array(window.getchannel("A"))
        except Exception as e:
            logger.warning("Nowcast probe failed: %s", e)
            return None
        if (alpha[c - 3:c + 4, c - 3:c + 4] > 30).any():
            return max(0.0, (ftime - now_ts) / 60.0)
    return None


def _draw_nowcast_outline(img: Image.Image, nowcast_rgba: Image.Image) -> bool:
    """
    Trace the projected precipitation edge as a dashed black line (in-place).

    Drawn as the boundary of the nowcast frame's precipitation mask, stippled so it
    reads as "projected" rather than observed. Roads carry a white casing and
    observed precipitation is filled, so a bare dashed line is unambiguous on the
    quantized 7-color output.

    Returns True if anything was drawn.
    """
    mask = np.array(nowcast_rgba.getchannel("A")) > 30
    if not mask.any():
        return False

    # 4-neighbour erosion, then boundary = mask minus its interior.
    up    = np.zeros_like(mask); up[1:, :]     = mask[:-1, :]
    down  = np.zeros_like(mask); down[:-1, :]  = mask[1:, :]
    left  = np.zeros_like(mask); left[:, 1:]   = mask[:, :-1]
    right = np.zeros_like(mask); right[:, :-1] = mask[:, 1:]
    boundary = mask & ~(up & down & left & right)

    # Thicken by one pixel so the dashes survive on a 7-color e-ink panel.
    thick = boundary.copy()
    thick[1:, :] |= boundary[:-1, :]
    thick[:, 1:] |= boundary[:, :-1]

    yy, xx = np.mgrid[0:thick.shape[0], 0:thick.shape[1]]
    dashed = thick & (((xx + yy) % 6) < 3)
    if not dashed.any():
        return False

    arr = np.array(img.convert("RGB"))
    arr[dashed] = _EINK_BLACK
    img.paste(Image.fromarray(arr, "RGB"), (0, 0))
    return True


def _draw_corner_labels(
    img: Image.Image, lines: List[str], config: dict, margin: int = 6
) -> None:
    """
    Draw a stacked white caption box in the bottom-left of the radar canvas.

    One owner for that corner keeps the radar timestamp, storm motion and nowcast
    captions from overlapping, and lets a single luminance snap cover all of them.
    The snap is required: anti-aliased glyph edges must not reach
    `_remap_radar_seven_color`, whose black cutoff is tuned for reflectivity and
    erodes small text into unreadable speckle.
    """
    if not lines:
        return

    draw = ImageDraw.Draw(img)
    font = get_font(11, config=config)

    widths, heights = [], []
    for line in lines:
        bb = draw.textbbox((0, 0), line, font=font)
        widths.append(bb[2] - bb[0])
        heights.append(bb[3] - bb[1])

    line_h = max(heights) + 4
    box_w  = max(widths) + 8
    box_h  = line_h * len(lines) + 4

    x0 = margin
    y0 = img.height - box_h - margin
    draw.rectangle([(x0, y0), (x0 + box_w, y0 + box_h)],
                   fill=(255, 255, 255), outline=(0, 0, 0))

    y = y0 + 3
    for line in lines:
        draw.text((x0 + 4, y), line, fill=(0, 0, 0), font=font)
        y += line_h

    box = (max(0, x0 - 1), max(0, y0 - 1),
           min(img.width, x0 + box_w + 2), min(img.height, y0 + box_h + 2))
    snapped = img.crop(box).convert("L").point(
        lambda px: 255 if px > 128 else 0
    ).convert("RGB")
    img.paste(snapped, box[:2])


def _fetch_rainviewer_image(
    lat: float, lon: float, zoom: int, width: int, height: int,
    color_scheme: int, headers: dict, config: dict = None,
) -> Tuple[Optional[Image.Image], Optional[int]]:
    """
    Fetch a composite radar image from RainViewer XYZ tiles centered on lat/lon.

    Layers OSM highway geometry over the radar for geographic orientation, overlays
    per-cell storm motion arrows computed from consecutive radar frames, traces the
    projected +30 min precipitation edge from the nowcast frames, and captions the
    bottom-left corner with the frame time, storm speed/heading and arrival estimate.

    Returns (RGB PIL Image sized exactly width×height, frame Unix timestamp), or (None, None).
    """
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
    latest_path  = latest_frame["path"]
    frame_ts     = latest_frame.get("time")
    logger.info("RainViewer: using frame %s", latest_path)

    cfg = config or {}

    tx_min, ty_min, tx_max, ty_max, crop_x, crop_y = _rv_tile_geometry(
        lat, lon, zoom, width, height, _RV_TILE_SIZE
    )

    # Road overlay — OSM interstate/trunk geometry, black core in a white casing
    show_map = cfg.get("rainviewer_map_overlay", True)
    road_img = None
    if show_map:
        road_img = _fetch_osm_roads(lat, lon, zoom, _RV_TILE_SIZE, width, height)

    # Start with white background, then layer: radar → roads on top
    canvas = Image.new("RGBA", (width, height), (255, 255, 255, 255))

    # Current radar frame
    curr_radar = _fetch_rv_frame(latest_path, tx_min, ty_min, tx_max, ty_max,
                                 zoom, _RV_TILE_SIZE, color_scheme, headers,
                                 crop_x, crop_y, width, height)
    canvas.alpha_composite(curr_radar)

    # Roads composited last so they appear on top of precipitation
    if road_img is not None:
        canvas.alpha_composite(road_img)

    result = canvas.convert("RGB")

    km_per_px = (40075.016686 * math.cos(math.radians(lat))) / (2 ** zoom) / _RV_TILE_SIZE
    caption_lines: List[str] = []

    # Storm motion: compare against the immediately previous frame (10-min gap).
    # Patch cross-correlation works best on consecutive frames where cell patterns
    # haven't changed substantially; longer gaps cause large growth/dissipation that
    # biases the bulk centroid and corrupts FFT-style methods.
    show_motion = cfg.get("rainviewer_storm_motion", True)
    if show_motion and len(radar_frames) >= 2:
        gap = 1
        prev_frame_meta = radar_frames[-(gap + 1)]
        prev_path  = prev_frame_meta["path"]
        actual_interval_min = (radar_frames[-1]["time"] - prev_frame_meta["time"]) / 60.0
        prev_radar = _fetch_rv_frame(prev_path, tx_min, ty_min, tx_max, ty_max,
                                     zoom, _RV_TILE_SIZE, color_scheme, headers,
                                     crop_x, crop_y, width, height)
        # Track patches once and reuse: the bulk median drives the caption, the
        # individual vectors drive the per-cell arrows.
        vectors = _patch_motion_vectors(prev_radar, curr_radar)
        motion = _compute_storm_motion(prev_radar, curr_radar)
        if motion:
            motion_lines = _draw_storm_motion_overlay(
                result, motion, curr_radar, cfg, km_per_px,
                actual_interval_min, vectors=vectors,
            )
            if motion_lines:
                caption_lines.extend(motion_lines)

    # Nowcast: dashed projected precipitation edge, plus a model-based arrival
    # estimate that supersedes the straight-line extrapolation above.
    if cfg.get("rainviewer_nowcast", True):
        nowcast_frames = (data.get("radar", {}).get("nowcast") or [])[:_NOWCAST_MAX_FRAMES]
        if nowcast_frames:
            eta = _nowcast_arrival_minutes(
                nowcast_frames, lat, lon, zoom, _RV_TILE_SIZE,
                color_scheme, headers, time.time(),
            )
            if eta is not None:
                caption_lines = [ln for ln in caption_lines if not ln.startswith("Rain here")]
                caption_lines.append(
                    "Rain here now" if eta < 2 else f"Rain here in ~{eta:.0f} min"
                )

            last = nowcast_frames[-1]
            try:
                ghost = _fetch_rv_frame(last["path"], tx_min, ty_min, tx_max, ty_max,
                                        zoom, _RV_TILE_SIZE, color_scheme, headers,
                                        crop_x, crop_y, width, height)
                if _draw_nowcast_outline(result, ghost):
                    ahead = max(0, round((last["time"] - time.time()) / 60.0))
                    caption_lines.append(f"Dashes: +{ahead} min")
            except Exception as e:
                logger.warning("Nowcast outline failed: %s", e)

    # Bottom-left caption stack (radar frame time first, then motion/nowcast)
    if frame_ts:
        caption_lines.insert(
            0, _dt.fromtimestamp(frame_ts).strftime("Radar: %-I:%M %p")
        )
    _draw_corner_labels(result, caption_lines, cfg)

    return result, frame_ts


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

    # Band edges are written as deg/360 rather than rounded decimals. Pure yellow
    # (255,255,0) — the canonical "moderate rain" ink, and extremely common in
    # radar imagery — has hue exactly 60°, i.e. 0.16666..., which is *below* a
    # literal 0.167 and was therefore being classified one tier too high, as
    # heavy-rain orange. deg/360 reproduces the same double the hue math produces,
    # so a boundary color lands in the band it names.
    precip = (s > 0.25) & (v > 0.35)

    # Blue: hue 160°-300° — cyans and blues (light precip)
    m = (h >= 160 / 360.0) & (h < 300 / 360.0) & precip
    out[m] = _EINK_BLUE

    # Green: hue 80°-160° — greens (light-moderate)
    m = (h >= 80 / 360.0) & (h < 160 / 360.0) & precip
    out[m] = _EINK_GREEN

    # Yellow: hue 60°-80° — yellows (moderate)
    m = (h >= 60 / 360.0) & (h < 80 / 360.0) & precip
    out[m] = _EINK_YELLOW

    # Orange: hue 30°-60° — oranges (heavy)
    m = (h >= 30 / 360.0) & (h < 60 / 360.0) & precip
    out[m] = _EINK_ORANGE

    # Red: hue 0°-30° or 300°-360° — reds and magentas (severe)
    m = ((h < 30 / 360.0) | (h >= 300 / 360.0)) & precip
    out[m] = _EINK_RED

    # Black: very dark pixels OR dark-but-saturated (extreme / hail markers).
    # Written last so it overrides any prior color assignment for dark pixels.
    m = (v < 0.18) | ((s > 0.20) & (v <= 0.35))
    out[m] = _EINK_BLACK

    return Image.fromarray(out, "RGB")


def _snap_region_2color(
    canvas: Image.Image, box: Tuple[int, int, int, int], color_a, color_b
) -> None:
    """Flatten a canvas region to exactly {color_a, color_b} by nearest-RGB distance.

    Any text/label drawn directly onto a canvas that will later pass through
    quantize_to_seven_colors()'s naive nearest-palette pass must have its
    anti-aliased edge pixels pre-resolved like this — otherwise those blended
    pixels land on whichever of the 7 palette colors happens to be nearest in
    raw RGB space (often a wrong, unrelated color), not on the two colors
    actually intended. Unlike a fixed luminance threshold, this works for any
    background color, not just white.
    """
    region = np.array(canvas.crop(box).convert("RGB"), dtype=np.float32)
    a = np.array(color_a, dtype=np.float32)
    b = np.array(color_b, dtype=np.float32)
    da = np.sum((region - a) ** 2, axis=-1)
    db = np.sum((region - b) ** 2, axis=-1)
    out = np.where(da[..., None] <= db[..., None], a, b).astype(np.uint8)
    canvas.paste(Image.fromarray(out, "RGB"), box[:2])


def _draw_seven_color_legend(
    canvas: Image.Image,
    frame_ts: Optional[int],
    station: str,
    config: dict,
    x0: int = 0,
    x1: Optional[int] = None,
    show_station_time: bool = True,
) -> None:
    """Draw the bottom legend strip showing all 7 e-ink colors with dBZ labels.

    x0/x1 scope the strip to a sub-range of the canvas width (e.g. just the
    radar side of panel mode); defaults draw across the full canvas as before.
    """
    W, H = canvas.size
    if x1 is None:
        x1 = W
    y0 = H - _LEGEND_H

    draw = ImageDraw.Draw(canvas)
    draw.rectangle([(x0, y0), (x1 - 1, H - 1)], fill=(255, 255, 255))
    draw.line([(x0, y0), (x1 - 1, y0)], fill=(0, 0, 0), width=1)

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

    x = x0 + 8
    for swatch_rgb, label, text_rgb in _SEVEN_COLOR_LEGEND:
        # Color swatch with black outline
        draw.rectangle(
            [(x, swatch_y), (x + swatch_side - 1, swatch_y + swatch_side - 1)],
            fill=swatch_rgb, outline=(0, 0, 0),
        )
        # Label to the right of swatch
        bb = draw.textbbox((0, 0), label, font=font)
        lh = bb[3] - bb[1]
        label_x = x + swatch_side + text_pad
        label_y = swatch_y + (swatch_side - lh) // 2
        draw.text((label_x, label_y), label, fill=(0, 0, 0), font=font)
        _snap_region_2color(
            canvas,
            (label_x + bb[0], label_y + bb[1], label_x + bb[2] + 1, label_y + bb[3] + 1),
            (255, 255, 255), (0, 0, 0),
        )
        x += swatch_side + text_pad + (bb[2] - bb[0]) + pad

    # Right side: station + frame timestamp
    if not show_station_time:
        return

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
    right_x = x1 - rw - 8
    right_y = y0 + (_LEGEND_H - rh) // 2
    draw.text((right_x, right_y), right_str, fill=(0, 0, 0), font=font)
    _snap_region_2color(
        canvas,
        (right_x + rb[0], right_y + rb[1], right_x + rb[2] + 1, right_y + rb[3] + 1),
        (255, 255, 255), (0, 0, 0),
    )


_NWS_STATES_CACHE = os.path.join("data", "nws_view_states.json")
_NWS_STATES_TTL   = 30 * 86400  # state geography does not move


def _view_state_codes(
    lat: float, lon: float, zoom: int, width: int, height: int, headers: dict,
) -> List[str]:
    """
    The US state/marine codes covering a radar view, for NWS alert area queries.

    Probes the four corners and the centre of the view via api.weather.gov/points
    and reads the state prefix off each forecast-zone id. Cached to disk for 30
    days — the answer only changes if the configured location or zoom changes.
    Points outside NWS coverage (ocean, across the border) simply 404 and are
    skipped.
    """
    cache_key = f"{lat:.4f},{lon:.4f},{zoom},{width}x{height}"
    try:
        if os.path.exists(_NWS_STATES_CACHE):
            with open(_NWS_STATES_CACHE) as fh:
                cached = json.load(fh)
            if cached.get("key") == cache_key and time.time() - cached.get("ts", 0) < _NWS_STATES_TTL:
                return cached.get("states", [])
    except Exception:
        pass

    lat_min, lon_min, lat_max, lon_max = _view_bounds(lat, lon, zoom, width, height)
    probes = [
        (lat, lon),
        (lat_max, lon_min), (lat_max, lon_max),
        (lat_min, lon_min), (lat_min, lon_max),
    ]

    nws_headers = dict(headers)
    nws_headers["Accept"] = "application/geo+json"
    states: List[str] = []
    for plat, plon in probes:
        try:
            resp = requests.get(
                f"https://api.weather.gov/points/{plat:.4f},{plon:.4f}",
                headers=nws_headers, timeout=10,
            )
            if resp.status_code != 200:
                continue
            zone = (resp.json().get("properties", {}) or {}).get("forecastZone", "") or ""
            code = zone.rstrip("/").split("/")[-1][:2].upper()
            if len(code) == 2 and code.isalpha() and code not in states:
                states.append(code)
        except Exception as e:
            logger.warning("NWS points lookup failed for %.4f,%.4f: %s", plat, plon, e)

    if states:
        try:
            os.makedirs(os.path.dirname(_NWS_STATES_CACHE), exist_ok=True)
            with open(_NWS_STATES_CACHE, "w") as fh:
                json.dump({"key": cache_key, "ts": time.time(), "states": states}, fh)
        except OSError:
            pass
        logger.info("NWS alert area: %s", ",".join(states))
    return states


def _fetch_nws_alerts(
    lat: float, lon: float, headers: dict,
    zoom: Optional[int] = None, width: int = 0, height: int = 0,
) -> List[Dict[str, Any]]:
    """
    Fetch active NWS alerts covering the radar view from api.weather.gov.

    When the view geometry is supplied, alerts are requested for every state the
    view touches; a point query would only return warnings covering the display
    location itself, so a tornado warning 60 km away — plainly visible on screen at
    zoom 7, where the canvas spans roughly ±130 km — would never be drawn. Falls
    back to a point query if the area lookup yields nothing.

    Returns a list of {"event", "severity", "polygon"} dicts, where polygon is a
    list of (lon, lat) vertices (or None if the alert has no storm-based geometry).
    Returns [] on any failure — the overlay is best-effort and never blocks radar.
    """
    url = f"https://api.weather.gov/alerts/active?point={lat:.4f},{lon:.4f}"
    if zoom is not None and width > 0 and height > 0:
        states = _view_state_codes(lat, lon, zoom, width, height, headers)
        if states:
            url = f"https://api.weather.gov/alerts/active?area={','.join(states)}"

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
        logger.info("NWS: %d active alert(s) in view area", len(alerts))
    return alerts


def _overlay_severe_alerts(
    canvas: Image.Image, center_lat: float, center_lon: float, zoom: int,
    region_w: int, region_h: int, headers: dict,
    x_off: int = 0, y_off: int = 0, config: Optional[dict] = None,
) -> None:
    """
    Draw active NWS storm-based warning polygons over a RainViewer-projected radar
    region. Uses the same Web-Mercator tile projection as _fetch_rainviewer_image so
    polygons line up with the radar tiles. Best-effort: any failure is swallowed.

    Everything is rendered onto a region-sized layer and pasted back, so a polygon
    that extends past the radar area is clipped instead of being painted across the
    conditions panel next to it.

    Alerts without polygon geometry (zone-based) are skipped here — the existing
    special-weather banner already surfaces those.
    """
    alerts = _fetch_nws_alerts(center_lat, center_lon, headers, zoom, region_w, region_h)
    if not alerts:
        return

    scale = (2 ** zoom) * _RV_TILE_SIZE
    cx_px, cy_px = _merc_xy(center_lat, center_lon, scale)

    def _to_pixel(lon: float, lat: float):
        wx, wy = _merc_xy(lat, lon, scale)
        return (region_w / 2.0 + (wx - cx_px), region_h / 2.0 + (wy - cy_px))

    # Local coordinates on a region-sized layer — anything off-region is clipped
    # by the paste rather than spilling onto the rest of the canvas.
    layer = canvas.crop((x_off, y_off, x_off + region_w, y_off + region_h)).convert("RGB")
    draw = ImageDraw.Draw(layer)
    RED = (255, 0, 0)
    WHITE = (255, 255, 255)

    try:
        lf = get_font(13, bold=True, config=config)
    except Exception:
        lf = ImageFont.load_default()

    drew_any = False
    for alert in alerts:
        poly = alert.get("polygon")
        if not poly or len(poly) < 3:
            continue
        pts = [_to_pixel(lon, lat) for lon, lat in poly]
        # Skip if the polygon is entirely outside the visible region
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        if max(xs) < 0 or min(xs) > region_w or max(ys) < 0 or min(ys) > region_h:
            continue
        drew_any = True

        # Bold red outline
        draw.line(pts + [pts[0]], fill=RED, width=3)

        # Event label anchored at the polygon's topmost vertex
        label = alert.get("event", "Alert")
        bb = draw.textbbox((0, 0), label, font=lf)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        top_i = int(np.argmin(ys))
        lx = max(2, min(xs[top_i], region_w - tw - 8))
        ly = max(2, min(ys[top_i], region_h - th - 6))
        box = (int(lx), int(ly), int(lx + tw + 6), int(ly + th + 4))
        draw.rectangle(list(box), fill=RED)
        draw.text((lx + 3, ly + 2), label, fill=WHITE, font=lf)
        # Anti-aliased white-on-red glyph edges must be resolved to exactly those
        # two colors before quantize_to_seven_colors() scatters them across
        # unrelated palette inks.
        _snap_region_2color(layer, box, RED, WHITE)

    if drew_any:
        canvas.paste(layer, (x_off, y_off))


def _redact(text: str, *secrets: str) -> str:
    """Blank out secret values before a string reaches the log."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def _fetch_lightning_strikes(
    lat: float, lon: float, headers: dict, client_id: str, client_secret: str,
) -> List[Dict[str, Any]]:
    """
    Fetch recent lightning strikes near (lat, lon) from the Xweather API.

    Free/standard tier limits: radius <=100km, data window is the past 5 minutes
    only, <=1000 strikes per query. Best-effort: returns [] on missing credentials
    or any failure — lightning is a nice-to-have overlay, never blocks radar.
    """
    if not client_id or not client_secret:
        return []
    # Credentials go in params, and the exception text is scrubbed before logging:
    # requests stringifies the full URL into its errors, which would otherwise
    # write client_secret into eink.log in plaintext.
    url = "https://data.api.xweather.com/lightning/closest"
    params = {
        "p": f"{lat:.4f},{lon:.4f}",
        "radius": "100km",
        "limit": "1000",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("Xweather lightning fetch failed: %s", _redact(str(e), client_secret))
        return []

    raw = data.get("response", data if isinstance(data, list) else [])
    strikes = []
    for item in raw:
        loc = item.get("loc", {}) or {}
        lat_s, lon_s = loc.get("lat"), loc.get("long")
        if lat_s is not None and lon_s is not None:
            strikes.append({"lat": lat_s, "lon": lon_s})
    if strikes:
        logger.info("Xweather: %d recent lightning strike(s) within 100km", len(strikes))
    return strikes


def _draw_lightning_overlay(
    canvas: Image.Image, strikes: List[Dict[str, Any]],
    center_lat: float, center_lon: float, zoom: int,
    region_w: int, region_h: int, x_off: int = 0, y_off: int = 0,
) -> None:
    """
    Draw recent lightning strikes as small white-haloed black dots over a
    RainViewer-projected radar region. Uses the same Web-Mercator projection as
    _overlay_severe_alerts so strikes line up with the radar tiles. Pure
    exact-palette fills — no anti-aliased content, so no pre-quantize snap needed.
    """
    if not strikes:
        return

    scale = (2 ** zoom) * _RV_TILE_SIZE
    cx_px, cy_px = _merc_xy(center_lat, center_lon, scale)

    def _to_pixel(lon: float, lat: float):
        wx, wy = _merc_xy(lat, lon, scale)
        return (region_w / 2.0 + (wx - cx_px), region_h / 2.0 + (wy - cy_px))

    # Drawn on a region-sized layer so a strike near the edge has its halo clipped
    # to the radar area instead of bleeding onto the conditions panel.
    layer = canvas.crop((x_off, y_off, x_off + region_w, y_off + region_h)).convert("RGB")
    draw = ImageDraw.Draw(layer)
    drew_any = False
    for s in strikes:
        x, y = _to_pixel(s["lon"], s["lat"])
        if not (0 <= x <= region_w and 0 <= y <= region_h):
            continue
        drew_any = True
        draw.ellipse([x - 6, y - 6, x + 6, y + 6], fill=(255, 255, 255))  # white halo
        draw.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(0, 0, 0))        # black core

    if drew_any:
        canvas.paste(layer, (x_off, y_off))


def _first_set(*values):
    """First value that is not None. Unlike `or`, a valid 0.0 (equator/prime
    meridian latitude or longitude) is not treated as missing."""
    for value in values:
        if value is not None:
            return value
    return None


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

    # In panel mode with RainViewer, carve out room at the bottom of the radar side
    # for the compact color-key legend (mirrors how seven_color mode already does
    # this). The dBZ legend only makes sense for RainViewer's classified colors, not
    # the raw NWS ridge GIF, so it's gated on radar_source too.
    panel_legend = (
        config.get("panel_legend", True)
        and radar_mode == "panel"
        and radar_source == "rainviewer"
    )
    # seven_color always carries the legend strip; panel mode carries it optionally.
    radar_h = (height - _LEGEND_H) if (panel_legend or radar_mode == "seven_color") else height

    if radar_source == "rainviewer":
        forecast_loc = config.get("forecast_location", {})
        rv_lat = _first_set(config.get("rainviewer_lat"), forecast_loc.get("latitude"))
        rv_lon = _first_set(config.get("rainviewer_lon"), forecast_loc.get("longitude"))
        if rv_lat is None or rv_lon is None:
            logger.error("RainViewer requires lat/lon — set forecast_location or rainviewer_lat/lon")
            return None, False, None
        rv_zoom = config.get("rainviewer_zoom", 7)
        rv_color = config.get("rainviewer_color_scheme", 4)
        # Pre-size to the exact radar canvas so mode scaling is a no-op
        rv_w = width - config.get("panel_width", 280) if radar_mode == "panel" else width
        radar_img, rv_frame_ts = _fetch_rainviewer_image(rv_lat, rv_lon, rv_zoom, rv_w, radar_h, rv_color, headers, config)
        if radar_img is None:
            logger.error("RainViewer fetch failed — cannot generate radar image.")
            return None, False, None
        # Snap to the exact 7-color e-ink palette here (HSV dBZ-tier classification) rather
        # than relying on quantize_to_seven_colors()'s naive per-pixel nearest-RGB pass below.
        # That naive pass scatters anti-aliased pixels (e.g. the radar caption text)
        # across mismatched palette colors, turning legible text into speckled noise.
        # This is also exactly what seven_color mode wants, so that branch reuses it
        # rather than fetching and remapping the whole composite a second time.
        radar_img = _remap_radar_seven_color(radar_img)
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
    # (x_off, y_off, w, h) of the radar area on final_img, for the geo overlays.
    # Every mode fetches RainViewer pre-sized to its radar area, so this region is
    # always an unscaled 1:1 copy of the tile projection.
    overlay_geom = None

    if radar_mode == "crop":
        scale = max(width / radar_img.width, height / radar_img.height)
        new_w = int(radar_img.width * scale)
        new_h = int(radar_img.height * scale)
        scaled_radar = radar_img.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - width) // 2
        top = (new_h - height) // 2
        processed_radar = scaled_radar.crop((left, top, left + width, top + height))
        overlay_geom = (0, 0, width, height)
    elif radar_mode == "fit":
        # Strip 24px NWS title bar (top) and color legend (bottom) — RIDGE GIF only.
        # A RainViewer composite has neither, so cropping it would discard 48px of
        # real radar (including the baked-in bottom-left caption stack) and
        # letterbox the result.
        if radar_source == "ridge":
            data_radar = radar_img.crop((0, 24, radar_img.width, radar_img.height - 24))
        else:
            data_radar = radar_img
        scale = min(width / data_radar.width, height / data_radar.height)
        new_w = int(data_radar.width * scale)
        new_h = int(data_radar.height * scale)
        processed_radar = data_radar.resize((new_w, new_h), Image.LANCZOS)
        x_offset = (width - new_w) // 2
        y_offset = (height - new_h) // 2
        primary_region = (x_offset, y_offset, x_offset + new_w, y_offset + new_h)
        overlay_geom = (x_offset, y_offset, new_w, new_h)
        final_img.paste(processed_radar, (x_offset, y_offset))
        processed_radar = None
    elif radar_mode == "panel":
        panel_w = config.get("panel_width", 280)
        radar_w = width - panel_w

        header_h = 21

        # Radar fills the radar area (full height, or height minus the legend strip) —
        # max scale fills the canvas, clipping ~1px from sides.
        scale = max(radar_w / radar_img.width, radar_h / radar_img.height)
        rw = int(radar_img.width * scale)
        rh = int(radar_img.height * scale)
        scaled_radar = radar_img.resize((rw, rh), Image.LANCZOS)
        x_off = (radar_w - rw) // 2   # negative = PIL auto-crops the sides
        y_off = (radar_h - rh) // 2
        final_img.paste(scaled_radar, (x_off, y_off))

        if panel_legend:
            _draw_seven_color_legend(final_img, rv_frame_ts, station, config,
                                      x0=0, x1=radar_w, show_station_time=False)

        # White panel background (full height on right side)
        draw_tmp = ImageDraw.Draw(final_img)
        draw_tmp.rectangle([(radar_w, 0), (width - 1, height - 1)], fill="white")

        # Fetch & render conditions (content starts below header_h)
        forecast_loc = config.get("forecast_location", {})
        lat = forecast_loc.get("latitude")
        lon = forecast_loc.get("longitude")
        conditions = (fetch_current_conditions(lat, lon, headers)
                      if lat is not None and lon is not None else None)
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

        # Draw colored hourly UV boxes (and any river/AQI status badge) AFTER
        # snap so their colors survive quantize
        if conditions and hourly_start_y is not None:
            badge_y = _draw_status_badges(final_img, config, radar_w, panel_w, hourly_start_y)
            _draw_hourly_uv_boxes(final_img, conditions, config, radar_w, panel_w, badge_y)

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
        # The header is drawn after the panel B/W snap (so the snap can't erode the
        # text), which leaves its anti-aliased white-on-header-color edges to reach
        # quantize_to_seven_colors() raw — they land on whichever ink is nearest in
        # RGB space, fringing the text orange. Resolve them to the two colors
        # actually in play.
        _snap_region_2color(final_img, (radar_w, 0, width, header_h),
                            header_color, (255, 255, 255))

        # Thin vertical separator (full height). Pure black, not grey: grey is far
        # enough from white to miss the quantizer's white threshold, and white is
        # not a palette entry, so a grey line lands on the nearest ink — orange.
        draw_tmp.line([(radar_w, 0), (radar_w, height - 1)], fill=(0, 0, 0), width=1)

        # Note: the radar frame timestamp is already baked into the bottom-left
        # corner of the radar tile itself (see the caption stack in
        # _fetch_rainviewer_image) — no separate label needed here.

        primary_region = (0, 0, radar_w, radar_h)
        overlay_geom = (0, 0, radar_w, radar_h)
        processed_radar = None
    elif radar_mode == "seven_color":
        # Full-screen 7-color radar: uses all Waveshare e-ink ink channels.
        # Pixels are classified by precipitation intensity via HSV hue, then
        # remapped to the 7 display colors. A legend strip at the bottom labels
        # each color with its approximate dBZ tier.
        if radar_source != "rainviewer":
            logger.error("seven_color mode requires radar_source: rainviewer")
            return None, False, None
        # radar_img was already fetched at width×radar_h and remapped above.
        final_img.paste(radar_img, (0, 0))
        _draw_seven_color_legend(final_img, rv_frame_ts, station, config)
        primary_region = (0, 0, width, radar_h)
        overlay_geom = (0, 0, width, radar_h)
        processed_radar = None
    else:
        raise ValueError(f"Invalid radar_mode '{radar_mode}'. Use 'crop', 'fit', 'panel', or 'seven_color'.")

    if processed_radar is not None:
        final_img.paste(processed_radar, (0, 0))

    # --- Geographic overlays -------------------------------------------------
    # Applied here rather than per-mode so crop/fit get them too, and always after
    # the radar has been pasted and remapped, so the pure red/black/white fills
    # survive to the final quantize. RainViewer only — the tile projection these
    # coordinates assume is not known for the NWS RIDGE GIF.
    if overlay_geom is not None and radar_source == "rainviewer":
        ox, oy, ow, oh = overlay_geom
        if config.get("radar_alerts_overlay", False):
            _overlay_severe_alerts(final_img, rv_lat, rv_lon, rv_zoom, ow, oh,
                                   headers, x_off=ox, y_off=oy, config=config)
        if config.get("lightning_overlay", False):
            strikes = _fetch_lightning_strikes(
                rv_lat, rv_lon, headers,
                config.get("xweather_client_id", ""), config.get("xweather_client_secret", ""),
            )
            _draw_lightning_overlay(final_img, strikes, rv_lat, rv_lon, rv_zoom,
                                    ow, oh, x_off=ox, y_off=oy)

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
        # Fetch failed or produced an unchanged image: fall back to the previous
        # render if there is one. On a cold start (or after `radar/` is cleaned)
        # there is not, and there is nothing to display or measure.
        if not os.path.exists(config["quantized_path"]):
            logger.error("Radar generation failed and no cached image exists — skipping cycle.")
            return None
        default_image_path = config["quantized_path"]
    default_percentage = calculate_non_bw_percentage(config["quantized_path"], region=default_region)
    logger.info("Default station (%s) has %.2f%% interesting pixels.", default_station, default_percentage)
    should_update = default_updated
    final_display_image = default_image_path

    save_state(STATE_FILE, state)

    if config.get('show_forecast_fallback', False) and default_percentage < config.get('interesting_threshold', 15):
        logger.info("No interesting radar found (%.2f%%). Falling back to forecast.", default_percentage)
        forecast_config = config.get('forecast_location', {})
        lat = forecast_config.get('latitude')
        lon = forecast_config.get('longitude')
        if lat is not None and lon is not None:
            forecast_data = get_detailed_forecast(lat, lon)
            if forecast_data:
                forecast_output = os.path.join(radar_folder, "forecast_display.bmp")
                forecast_path = generate_forecast_image(config, forecast_data, forecast_output)
                if forecast_path:
                    final_display_image = forecast_path
                    should_update = True

    current_station = default_station
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
