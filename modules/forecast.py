import requests
import yaml
from PIL import Image, ImageDraw, ImageFont
import platform
import os

if platform.system() == "Linux":
    from display import display_color_image


def load_config(config_file='config.yml'):
    """Load configuration from YAML file."""
    with open(config_file, 'r') as f:
        return yaml.safe_load(f)


def get_detailed_forecast(lat, lon):
    """Fetch the detailed forecast from the National Weather Service API."""
    try:
        points_url = f"https://api.weather.gov/points/{lat},{lon}"
        headers = {"User-Agent": "WeatherDisplay/1.0 (contact@example.com)"}

        print(f"Fetching forecast data for coordinates: {lat}, {lon}")
        points_response = requests.get(points_url, headers=headers)
        points_response.raise_for_status()
        points_data = points_response.json()

        forecast_url = points_data['properties']['forecast']
        location_name = points_data['properties']['relativeLocation']['properties']['city']
        state = points_data['properties']['relativeLocation']['properties']['state']

        print(f"Location: {location_name}, {state}")

        forecast_response = requests.get(forecast_url, headers=headers)
        forecast_response.raise_for_status()
        forecast_data = forecast_response.json()

        return {
            'location': f"{location_name}, {state}",
            'periods': forecast_data['properties']['periods']
        }

    except requests.exceptions.RequestException as e:
        print(f"Error fetching forecast: {e}")
        return None
    except KeyError as e:
        print(f"Error parsing forecast data: {e}")
        return None


def wrap_text(text, font, max_width, draw):
    """Wrap text to fit within a specified width."""
    lines = []
    words = text.split()
    current_line = []

    for word in words:
        test_line = ' '.join(current_line + [word])
        bbox = draw.textbbox((0, 0), test_line, font=font)
        text_width = bbox[2] - bbox[0]

        if text_width <= max_width:
            current_line.append(word)
        else:
            if current_line:
                lines.append(' '.join(current_line))
                current_line = [word]
            else:
                lines.append(word)

    if current_line:
        lines.append(' '.join(current_line))

    return lines


def build_forecast_blocks(forecast_data, num_periods):
    blocks = []
    periods = forecast_data.get('periods', [])
    for period in periods[:num_periods]:
        period_name = period.get('name', 'Unknown')
        temp = period.get('temperature', 'N/A')
        temp_unit = period.get('temperatureUnit', 'F')
        short_forecast = period.get('shortForecast', '')
        subtitle = f" {temp}\u00b0{temp_unit} - {short_forecast}"
        detail = period.get('detailedForecast', '')
        blocks.append({'name': period_name, 'subtitle': subtitle, 'detail': detail})
    return blocks


def calculate_block_height(block, header_font, detail_font, max_width, draw, line_spacing, section_spacing):
    height = 0
    name_with_colon = block['name'] + ":"
    name_bbox = draw.textbbox((0, 0), name_with_colon, font=header_font)
    name_w = name_bbox[2] - name_bbox[0]
    name_h = name_bbox[3] - name_bbox[1]

    sub_bbox = draw.textbbox((0, 0), block['subtitle'], font=detail_font)
    sub_w = sub_bbox[2] - sub_bbox[0]
    sub_h = sub_bbox[3] - sub_bbox[1]

    header_line_h = max(name_h, sub_h)
    sample_bbox = draw.textbbox((0, 0), "Ag", font=detail_font)
    detail_line_h = sample_bbox[3] - sample_bbox[1]

    if name_w + sub_w <= max_width:
        height += header_line_h + line_spacing
    else:
        height += header_line_h + line_spacing
        wrapped_sub = wrap_text(block['subtitle'].strip(), detail_font, max_width, draw)
        for line in wrapped_sub:
            height += detail_line_h + line_spacing

    if block['detail']:
        wrapped = wrap_text(block['detail'], detail_font, max_width, draw)
        for line in wrapped:
            height += detail_line_h + line_spacing

    height += section_spacing
    return height


def calculate_total_height(blocks, header_font, detail_font, max_width, draw, line_spacing=2, section_spacing=8):
    total = 0
    for block in blocks:
        total += calculate_block_height(block, header_font, detail_font, max_width, draw, line_spacing, section_spacing)
    return total


def find_best_font_size(blocks, font_path, max_width, max_height, draw, max_font_size, min_font_size, header_scale=1.3):
    best_size = min_font_size
    low, high = min_font_size, max_font_size

    while low <= high:
        mid = (low + high) // 2
        header_size = int(mid * header_scale)
        try:
            detail_font = ImageFont.truetype(font_path, mid)
            header_font = ImageFont.truetype(font_path, header_size)
        except Exception:
            return min_font_size

        total = calculate_total_height(blocks, header_font, detail_font, max_width, draw)
        if total <= max_height:
            best_size = mid
            low = mid + 1
        else:
            high = mid - 1

    return best_size


def generate_forecast_image(config, forecast_data, output_path=None):
    """Generate an image displaying the detailed forecast."""
    if not forecast_data:
        print("No forecast data available")
        return None

    width = config.get("width", 800)
    height = config.get("height", 480)
    background_color = config.get("background_color", "white")
    text_color = config.get("text_color", "black")
    font_path = config.get("font_path", "/Library/Fonts/Arial Unicode.ttf")

    forecast_cfg = config.get("forecast_display", {})
    max_font_size = forecast_cfg.get("max_font_size", 100)
    min_font_size = forecast_cfg.get("min_font_size", 8)
    num_periods = forecast_cfg.get("num_periods", 5)
    margin = forecast_cfg.get("margin", 10)

    if output_path is None:
        output_path = config.get("output_path", "forecast_display.bmp")

    img = Image.new("RGB", (width, height), color=background_color)
    draw = ImageDraw.Draw(img)

    max_text_width = width - 2 * margin
    available_height = height - 2 * margin

    blocks = build_forecast_blocks(forecast_data, num_periods)

    if not blocks:
        print("No forecast periods available")
        img.save(output_path)
        return output_path

    header_scale = forecast_cfg.get("header_scale", 1.3)

    best_size = find_best_font_size(
        blocks, font_path, max_text_width, available_height,
        draw, max_font_size, min_font_size, header_scale
    )
    header_size = int(best_size * header_scale)
    print(f"Auto-selected font size: {best_size} (period names: {header_size})")

    try:
        detail_font = ImageFont.truetype(font_path, best_size)
        header_font = ImageFont.truetype(font_path, header_size)
    except Exception:
        print("Could not load custom font, using default")
        detail_font = ImageFont.load_default()
        header_font = detail_font

    y_offset = margin
    line_spacing = 2
    section_spacing = 8

    header_ascent, header_descent = header_font.getmetrics()
    detail_ascent, detail_descent = detail_font.getmetrics()

    sample_bbox = draw.textbbox((0, 0), "Ag", font=detail_font)
    detail_line_h = sample_bbox[3] - sample_bbox[1]

    for block in blocks:
        name_with_colon = block['name'] + ":"
        name_bbox = draw.textbbox((0, 0), name_with_colon, font=header_font)
        name_w = name_bbox[2] - name_bbox[0]
        name_h = name_bbox[3] - name_bbox[1]

        sub_bbox = draw.textbbox((0, 0), block['subtitle'], font=detail_font)
        sub_w = sub_bbox[2] - sub_bbox[0]
        sub_h = sub_bbox[3] - sub_bbox[1]

        header_line_h = max(name_h, sub_h)

        name_y = y_offset
        sub_y = y_offset + (header_ascent - detail_ascent)

        draw.text((margin, name_y), name_with_colon, fill=text_color, font=header_font)

        if name_w + sub_w <= max_text_width:
            draw.text((margin + name_w, sub_y), block['subtitle'], fill=text_color, font=detail_font)
            y_offset += header_line_h + line_spacing
        else:
            y_offset += header_line_h + line_spacing
            wrapped_sub = wrap_text(block['subtitle'].strip(), detail_font, max_text_width, draw)
            for line in wrapped_sub:
                draw.text((margin, y_offset), line, fill=text_color, font=detail_font)
                y_offset += detail_line_h + line_spacing

        if block['detail']:
            wrapped = wrap_text(block['detail'], detail_font, max_text_width, draw)
            for line in wrapped:
                draw.text((margin, y_offset), line, fill=text_color, font=detail_font)
                y_offset += detail_line_h + line_spacing

        y_offset += section_spacing

    location = forecast_data.get('location', '')
    if location:
        loc_bbox = draw.textbbox((0, 0), location, font=detail_font)
        loc_w = loc_bbox[2] - loc_bbox[0]
        draw.text((width - loc_w - margin, margin), location, fill=text_color, font=detail_font)

    img.save(output_path)
    print(f"Forecast image saved to: {output_path}")
    return output_path


def generate(config):
    """Module interface: generate forecast image and return output path."""
    forecast_cfg = config.get('forecast_location', {})
    lat = forecast_cfg.get('latitude')
    lon = forecast_cfg.get('longitude')

    if not lat or not lon:
        print("No forecast_location coordinates in config")
        return None

    forecast_data = get_detailed_forecast(lat, lon)
    if not forecast_data:
        print("Failed to fetch forecast data")
        return None

    output_path = config.get('forecast_display', {}).get('output_path', 'forecast_display.bmp')
    return generate_forecast_image(config, forecast_data, output_path)
