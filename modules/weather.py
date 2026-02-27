import time
import json
import requests
import io
from PIL import Image, ImageDraw, ImageFont
import qrcode
from modules.special_weather import get_special_weather_messages, get_alert_headline
from eink_generator import load_config
from modules.forecast import get_detailed_forecast, generate_forecast_image
from modules.moon_phase import _moon_age, _phase_fraction, _phase_name, _illumination
from datetime import datetime as _dt, date as _date
import math
import platform
import os

NWS_KM_PER_PX = 1.533  # km per pixel in original NWS 600×550 radar image

# ---------------------------------------------------------------------------
# Current-conditions cache (5-minute TTL to avoid repeated API calls)
# ---------------------------------------------------------------------------
_conditions_cache = {"data": None, "ts": 0}
_CONDITIONS_TTL = 300  # seconds


def _deg_to_compass(deg):
    """Convert wind direction degrees (0-360) to 16-point compass string."""
    directions = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return directions[round(deg / 22.5) % 16]


def _wmo_description(code):
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


def fetch_current_conditions(lat, lon, headers):
    """
    Fetch current weather conditions from Open-Meteo (no API key required).
    Results are cached for _CONDITIONS_TTL seconds.
    Returns a dict of weather data or None on failure.
    """
    global _conditions_cache
    now = time.time()
    if _conditions_cache["data"] is not None and now - _conditions_cache["ts"] < _CONDITIONS_TTL:
        return _conditions_cache["data"]

    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
        f"weather_code,surface_pressure,"
        f"wind_speed_10m,wind_direction_10m,wind_gusts_10m,uv_index,is_day"
        f"&hourly=visibility,surface_pressure,temperature_2m,weather_code,precipitation_probability"
        f"&daily=sunrise,sunset,precipitation_sum,temperature_2m_max,temperature_2m_min"
        f"&wind_speed_unit=mph"
        f"&temperature_unit=fahrenheit"
        f"&precipitation_unit=inch"
        f"&timezone=auto"
        f"&past_days=7"
        f"&forecast_days=1"
    )

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[weather] Failed to fetch current conditions: {e}")
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

        # Sunrise / Sunset (last daily entry = today)
        sunrise_list = daily.get("sunrise", [])
        sunset_list = daily.get("sunset", [])
        sunrise = _parse_time(sunrise_list[-1]) if sunrise_list else "N/A"
        sunset = _parse_time(sunset_list[-1]) if sunset_list else "N/A"

        # High / Low today
        high_today = int(round(daily.get("temperature_2m_max", [0])[-1] or 0))
        low_today = int(round(daily.get("temperature_2m_min", [0])[-1] or 0))

        # Moon phase (pure math, no network)
        age = _moon_age()
        fraction = _phase_fraction(age)
        moon_name = _phase_name(fraction)
        moon_illum = _illumination(fraction)

        # Pressure: hPa → inHg + trend from last 2 hourly values
        pressure_hpa = current.get("surface_pressure", 1013.25)
        pressure_inhg = round(pressure_hpa * 0.02953, 2)
        pressure_hourly = hourly.get("surface_pressure", [])
        if cur_idx >= 3 and pressure_hourly and pressure_hourly[cur_idx] and pressure_hourly[cur_idx - 3]:
            pdiff = pressure_hourly[cur_idx] - pressure_hourly[cur_idx - 3]
            pressure_trend = "↑" if pdiff > 0.8 else ("↓" if pdiff < -0.8 else "→")
        else:
            pressure_trend = ""

        # Wind direction
        wind_dir = _deg_to_compass(current.get("wind_direction_10m", 0))

        # Weather description
        weather_code = current.get("weather_code", 0)
        weather_desc = _wmo_description(weather_code)

        # Next 3 hourly slots after current hour
        hourly_temps  = hourly.get("temperature_2m", [])
        hourly_codes  = hourly.get("weather_code", [])
        hourly_precip = hourly.get("precipitation_probability", [])
        hourly_forecast = []
        for i in range(1, 4):
            idx = cur_idx + i
            if 0 <= idx < len(hourly_times):
                try:
                    slot_dt = _dt.fromisoformat(hourly_times[idx])
                    label = slot_dt.strftime("%-I %p")
                except Exception:
                    label = hourly_times[idx][-5:]
                temp_h  = int(round(hourly_temps[idx]))  if idx < len(hourly_temps)  else None
                code_h  = hourly_codes[idx]               if idx < len(hourly_codes)  else 0
                precip_h = hourly_precip[idx]             if idx < len(hourly_precip) else 0
                hourly_forecast.append({
                    "time":   label,
                    "temp":   temp_h,
                    "desc":   _wmo_description(code_h),
                    "precip": int(precip_h) if precip_h is not None else 0,
                })

        result = {
            "temp":         int(round(current.get("temperature_2m", 0))),
            "feels_like":   int(round(current.get("apparent_temperature", 0))),
            "humidity":     int(current.get("relative_humidity_2m", 0)),
            "weather_code": weather_code,
            "weather_desc": weather_desc,
            "is_day":       bool(current.get("is_day", 1)),
            "pressure":        pressure_inhg,
            "pressure_trend":  pressure_trend,
            "wind_speed":   int(round(current.get("wind_speed_10m", 0))),
            "wind_dir":     wind_dir,
            "wind_gust":    int(round(current.get("wind_gusts_10m", 0))),
            "uv_index":     round(current.get("uv_index", 0), 1),
            "visibility":   visibility_mi,
            "rain_today":   round(rain_today, 2) if rain_today else 0.0,
            "rain_7day":    round(rain_7day, 2),
            "sunrise":      sunrise,
            "sunset":       sunset,
            "high_today":   high_today,
            "low_today":    low_today,
            "moon_name":    moon_name,
            "moon_illum":   moon_illum,
            "hourly_forecast": hourly_forecast,
        }

        _conditions_cache["data"] = result
        _conditions_cache["ts"] = now
        print(f"[weather] Conditions fetched: {result['temp']}°F, {result['weather_desc']}")
        return result

    except Exception as e:
        print(f"[weather] Error parsing conditions response: {e}")
        return None


def draw_conditions_panel(canvas, conditions, config, panel_x, panel_w):
    """
    Draw the current-conditions data panel on an existing PIL Image.
    panel_x: left edge of the panel in canvas pixels
    panel_w: width of the panel in pixels
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
        return y + 8

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

    # --- Black header bar — height matches NWS radar banner (~38px) ---
    header_h = 38
    draw.rectangle([(panel_x, 0), (panel_x + panel_w - 1, header_h - 1)], fill=BLACK)
    forecast_loc = config.get("forecast_location", {})
    loc_name = forecast_loc.get("name", "")
    weekday = _date.today().strftime("%a")
    hdr_str = f"{loc_name}  {weekday}"
    hdr_avail_h = header_h - 6
    for size in range(20, 9, -1):
        hdr_font = _font(size)
        bb = draw.textbbox((0, 0), hdr_str, font=hdr_font)
        if (bb[2] - bb[0]) <= text_w and (bb[3] - bb[1]) <= hdr_avail_h:
            break
    bb = draw.textbbox((0, 0), hdr_str, font=hdr_font)
    draw.text((text_x, (header_h - (bb[3] - bb[1])) // 2), hdr_str, fill=WHITE, font=hdr_font)

    if conditions is None:
        draw.text((panel_x + panel_w // 2, height // 2), "No data",
                  fill=BLACK, font=_font(18), anchor="mm")
        return

    y = header_h + 6

    # Temperature — auto-size 56–72px to fill panel width
    temp_str = f"{conditions['temp']}°F"
    for size in range(72, 55, -2):
        font = _font(size)
        if draw.textbbox((0, 0), temp_str, font=font)[2] <= text_w:
            break
    draw.text((text_x, y), temp_str, fill=BLACK, font=font)
    y += draw.textbbox((0, 0), temp_str, font=font)[3] + 4

    # Feels like / description — auto-size 13→9px to fit width
    feels_desc = f"Feels like {conditions['feels_like']}°F  \u2022  {conditions['weather_desc']}"
    for size in range(13, 8, -1):
        font = _font(size)
        if draw.textbbox((0, 0), feels_desc, font=font)[2] <= text_w:
            break
    draw.text((text_x, y), feels_desc, fill=BLACK, font=font)
    y += draw.textbbox((0, 0), feels_desc, font=font)[3] + 4

    # Separator
    y = _separator(y)

    # Two-column data rows — pressure row gets a drawn trend arrow
    label_font = _font(15)
    value_font = _font(18)
    row_h = 26
    raw_trend = conditions.get("pressure_trend", "")
    trend_dir = "up" if raw_trend == "↑" else ("down" if raw_trend == "↓" else ("steady" if raw_trend == "→" else ""))

    rows = [
        ("Humidity",   f"{conditions['humidity']}%",                                              ""),
        ("Pressure",   f"{conditions['pressure']} inHg",                                          trend_dir),
        ("Visibility", f"{conditions['visibility']} mi",                                          ""),
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

    # Sunrise / Sunset rows
    sun_font = _font(18)
    for label, value in [("Sunrise", conditions["sunrise"]), ("Sunset", conditions["sunset"])]:
        draw.text((text_x, y), label, fill=BLACK, font=sun_font)
        val_bbox = draw.textbbox((0, 0), value, font=sun_font)
        draw.text((right_x - (val_bbox[2] - val_bbox[0]), y), value, fill=BLACK, font=sun_font)
        y += row_h

    # Separator
    y = _separator(y)

    # Moon phase
    moon_str = f"{conditions['moon_name']}  {conditions['moon_illum']}%"
    draw.text((text_x, y), moon_str, fill=BLACK, font=_font(15))
    y += row_h

    # Today High / Low
    hl_str = f"Today  H{conditions['high_today']}\u00b0 / L{conditions['low_today']}\u00b0"
    draw.text((text_x, y), hl_str, fill=BLACK, font=_font(15))
    y += row_h

    # Hourly forecast (next 3 hours)
    hourly = conditions.get("hourly_forecast", [])
    if hourly and y + 10 < height:
        y = _separator(y)
        hr_font = _font(13)
        for slot in hourly:
            if y + 16 > height:
                break
            desc_short = slot["desc"][:12]
            line = f"{slot['time']}  {slot['temp']}\u00b0  {desc_short}"
            if slot["precip"]:
                line += f"  {slot['precip']}%"
            draw.text((text_x, y), line, fill=BLACK, font=hr_font)
            y += 18


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
    print(f"Quantized image saved to {output_path}")


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

    radar_url = f"https://radar.weather.gov/ridge/standard/{station}_0.gif"

    if config.get("url_qr_loop", True):
        radar_url_qr = f"https://radar.weather.gov/ridge/standard/{station}_loop.gif"
    else:
        radar_url_qr = f'https://radar.weather.gov/station/{station.lower()}/standard'

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    for attempt in range(3):
        response = requests.get(radar_url, headers=headers)
        if response.status_code == 200:
            break
        elif response.status_code == 404 and attempt < 2:
            print(f"Image not found (404). Retrying... (Attempt {attempt + 1})")
            time.sleep(2)
        else:
            print(f"Failed to fetch image. Status code: {response.status_code}")
            return None, False, None

    content_type = response.headers.get("Content-Type", "")
    if "image" not in content_type:
        print(f"Unexpected content type: {content_type}")
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

        # Strip NWS title bar (top 38px) and color legend (bottom 38px) so the
        # data area aligns with the conditions panel header bottom edge.
        data_radar = radar_img.crop((0, 38, radar_img.width, radar_img.height - 38))
        scale = max(radar_w / data_radar.width, height / data_radar.height)
        rw = int(data_radar.width * scale)
        rh = int(data_radar.height * scale)
        scaled_radar = data_radar.resize((rw, rh), Image.LANCZOS)
        left_crop = (rw - radar_w) // 2
        top_crop  = (rh - height)  // 2
        processed_radar = scaled_radar.crop((left_crop, top_crop,
                                             left_crop + radar_w, top_crop + height))
        final_img.paste(processed_radar, (0, 0))

        # White panel background
        draw_tmp = ImageDraw.Draw(final_img)
        draw_tmp.rectangle([(radar_w, 0), (width - 1, height - 1)], fill="white")

        # Thin vertical separator
        draw_tmp.line([(radar_w, 0), (radar_w, height - 1)], fill=(180, 180, 180), width=1)

        # Fetch & render current conditions
        forecast_loc = config.get("forecast_location", {})
        lat = forecast_loc.get("latitude")
        lon = forecast_loc.get("longitude")
        conditions = fetch_current_conditions(lat, lon, headers) if lat and lon else None
        draw_conditions_panel(final_img, conditions, config, radar_w, panel_w)

        primary_region = (0, 0, radar_w, height)   # interesting% measured on radar only
        processed_radar = None

        # Snap panel pixels to pure black/white so anti-aliased text fringe
        # (grey ~90-140) doesn't get quantized to orange in the 7-color palette.
        panel_box = (radar_w, 0, width, height)
        panel_bw = final_img.crop(panel_box).convert("L").point(
            lambda px: 255 if px > 128 else 0
        ).convert("RGB")
        final_img.paste(panel_bw, (radar_w, 0))
    else:
        raise ValueError(f"Invalid radar_mode '{radar_mode}'. Use 'crop', 'fit', or 'panel'.")

    if processed_radar is not None:
        final_img.paste(processed_radar, (0, 0))

    old_quant = None
    if os.path.exists(quantized_output_path):
        old_quant = Image.open(quantized_output_path).convert("RGB")

    if config.get("check_special_weather", True) and special_msg:
        try:
            special_url = config.get('special_url', "https://forecast.weather.gov/showsigwx.php?warnzone=TNZ027&warncounty=TNC037&firewxzone=TNZ027&local_place1=Nashville%20TN")
            qr_topright = qrcode.make(special_url).resize((138, 138), Image.LANCZOS)
            _banner_h = config.get("alert_banner_height", 40)
            final_img.paste(qr_topright, (final_img.width - qr_topright.width - 2, _banner_h + 2))
        except Exception as e:
            print(f"Error adding special weather QR code: {e}")

    draw = ImageDraw.Draw(final_img)

    # --- Alert Banner ---
    if config.get("check_special_weather", True) and special_msg:
        alert_banner_height = config.get("alert_banner_height", 40)
        headline = get_alert_headline(special_msg)
        if headline:
            draw.rectangle([(0, 0), (width - 1, alert_banner_height - 1)], fill=(0, 0, 0))
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
    print(f"Saved final weather image to {output_path}")

    more_colors = config.get('more_colors', False)
    quantize_to_seven_colors(output_path, quantized_output_path, more_colors, threshold=75)
    new_quant = Image.open(quantized_output_path).convert("RGB")
    if old_quant is not None and images_are_equal(old_quant, new_quant):
        print(f"Station {station}: Quantized image unchanged.")
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
        print(f"Full scan: Station {station} -> {perc:.2f}%")
    config["radar_mode"] = saved_mode
    return percentages


def update_top5(percentages):
    sorted_stations = sorted(percentages.items(), key=lambda x: x[1], reverse=True)
    top5 = sorted_stations[:5]
    print("Top 5 stations from full scan:", top5)
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
    print(f"Default station ({default_station}) has {default_percentage:.2f}% interesting pixels.")
    should_update = default_updated
    final_display_image = default_image_path

    save_state(STATE_FILE, state)

    best_station = None
    best_percentage = default_percentage
    if config.get('interesting_station', True) and default_percentage < config.get('interesting_threshold', 15) and top5_list:
        best_percentage = 0
        best_image_path = None
        for station, _ in top5_list:
            config["station"] = {"name": station}
            config["output_path"] = os.path.join(radar_folder, f"eink_display_{station}.bmp")
            config["quantized_path"] = os.path.join(radar_folder, f"eink_quantized_display_{station}.bmp")
            station_entry = next((s for s in config.get("stations", []) if s["name"] == station), {})
            config['station']['location'] = station_entry.get("location", "Unknown Location")
            image_path, updated, _ = generate_weather_image(config, special_msg=special_msg)
            # Use cached quantized image if it exists and hasn't changed
            if image_path is None:
                image_path = config["quantized_path"]
            if not os.path.exists(image_path):
                continue
            should_update = True
            perc = calculate_non_bw_percentage(config["quantized_path"])
            if perc > best_percentage:
                best_percentage = perc
                best_station = station
                best_image_path = image_path
        if best_station and best_image_path is not None:
            print(f"Switching display to station {best_station} with {best_percentage:.2f}%.")
            final_display_image = best_image_path
    else:
        print("Default station is dynamic enough; using default image.")

    final_percentage = best_percentage if best_station else default_percentage

    if config.get('show_forecast_fallback', False) and final_percentage < config.get('interesting_threshold', 15):
        print(f"No interesting radar found (best: {final_percentage:.2f}%). Falling back to forecast.")
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

    if should_update and final_display_image and config.get('interesting_station', True):
        if now - state.get("last_full_scan", 0) >= full_scan_interval:
            state["last_full_scan"] = now
            save_state(STATE_FILE, state)
            print("Running full station refresh...")
            percentages = full_station_scan(config, skip_station=default_station)
            percentages[default_station] = default_percentage
            top5_list = update_top5(percentages)
            top5_data = [{"station": s, "percentage": p} for s, p in top5_list]
            state["top5"] = top5_data
            save_state(STATE_FILE, state)

    return final_display_image if should_update else None


def main():
    config = load_config('config.yml')
    output = generate(config)
    if output and platform.system() == "Linux":
        display_color_image(output)
    elif output:
        print(f"Generated: {output} (display skipped on macOS)")
    else:
        print("No image changes detected.")


if __name__ == '__main__':
    main()
