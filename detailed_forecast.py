import requests
import yaml
from PIL import Image, ImageDraw, ImageFont
import textwrap
import platform
import os

# Import display function only on Linux (Raspberry Pi)
if platform.system() == "Linux":
    from display import display_color_image


def load_config(config_file='config.yml'):
    """Load configuration from YAML file."""
    with open(config_file, 'r') as f:
        return yaml.safe_load(f)


def get_detailed_forecast(lat, lon):
    """
    Fetch the detailed forecast from the National Weather Service API.

    Args:
        lat (float): Latitude coordinate
        lon (float): Longitude coordinate

    Returns:
        dict: Forecast data including periods with detailed descriptions
    """
    try:
        # First, get the forecast grid endpoint
        points_url = f"https://api.weather.gov/points/{lat},{lon}"
        headers = {
            "User-Agent": "WeatherDisplay/1.0 (contact@example.com)"
        }

        print(f"Fetching forecast data for coordinates: {lat}, {lon}")
        points_response = requests.get(points_url, headers=headers)
        points_response.raise_for_status()
        points_data = points_response.json()

        # Get the forecast URL from the points data
        forecast_url = points_data['properties']['forecast']
        location_name = points_data['properties']['relativeLocation']['properties']['city']
        state = points_data['properties']['relativeLocation']['properties']['state']

        print(f"Location: {location_name}, {state}")

        # Fetch the detailed forecast
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
    """
    Wrap text to fit within a specified width.

    Args:
        text (str): Text to wrap
        font: PIL ImageFont object
        max_width (int): Maximum width in pixels
        draw: PIL ImageDraw object

    Returns:
        list: List of wrapped text lines
    """
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
                # Word is too long, add it anyway
                lines.append(word)

    if current_line:
        lines.append(' '.join(current_line))

    return lines


def build_forecast_blocks(forecast_data, num_periods):
    """
    Build structured forecast blocks.
    Each block has a 'name' (big font), 'subtitle' (normal font, same line),
    and 'detail' (normal font, wrapped below).
    """
    blocks = []
    periods = forecast_data.get('periods', [])
    for period in periods[:num_periods]:
        period_name = period.get('name', 'Unknown')
        temp = period.get('temperature', 'N/A')
        temp_unit = period.get('temperatureUnit', 'F')
        short_forecast = period.get('shortForecast', '')

        subtitle = f" {temp}\u00b0{temp_unit} - {short_forecast}"
        detail = period.get('detailedForecast', '')
        blocks.append({
            'name': period_name,
            'subtitle': subtitle,
            'detail': detail,
        })
    return blocks


def calculate_block_height(block, header_font, detail_font, max_width, draw, line_spacing, section_spacing):
    """Calculate height for a single forecast block."""
    height = 0

    # Measure header line height using textbbox on actual text
    name_with_colon = block['name'] + ":"
    name_bbox = draw.textbbox((0, 0), name_with_colon, font=header_font)
    name_w = name_bbox[2] - name_bbox[0]
    name_h = name_bbox[3] - name_bbox[1]

    sub_bbox = draw.textbbox((0, 0), block['subtitle'], font=detail_font)
    sub_w = sub_bbox[2] - sub_bbox[0]
    sub_h = sub_bbox[3] - sub_bbox[1]

    # Header line uses the taller of the two fonts
    header_line_h = max(name_h, sub_h)

    # Measure a representative detail line height
    sample_bbox = draw.textbbox((0, 0), "Ag", font=detail_font)
    detail_line_h = sample_bbox[3] - sample_bbox[1]

    if name_w + sub_w <= max_width:
        height += header_line_h + line_spacing
    else:
        height += header_line_h + line_spacing
        wrapped_sub = wrap_text(block['subtitle'].strip(), detail_font, max_width, draw)
        for line in wrapped_sub:
            height += detail_line_h + line_spacing

    # Detail text
    if block['detail']:
        wrapped = wrap_text(block['detail'], detail_font, max_width, draw)
        for line in wrapped:
            height += detail_line_h + line_spacing

    height += section_spacing
    return height


def calculate_total_height(blocks, header_font, detail_font, max_width, draw, line_spacing=2, section_spacing=8):
    """Calculate total height for all forecast blocks."""
    total = 0
    for block in blocks:
        total += calculate_block_height(block, header_font, detail_font, max_width, draw, line_spacing, section_spacing)
    return total


def find_best_font_size(blocks, font_path, max_width, max_height, draw, max_font_size, min_font_size, header_scale=1.3):
    """
    Binary search for the largest detail font size that fits all blocks on the display.
    Header font (period name only) is scaled up by header_scale.
    """
    best_size = min_font_size
    low, high = min_font_size, max_font_size

    while low <= high:
        mid = (low + high) // 2
        header_size = int(mid * header_scale)
        try:
            detail_font = ImageFont.truetype(font_path, mid)
            header_font = ImageFont.truetype(font_path, header_size)
        except:
            return min_font_size

        total = calculate_total_height(blocks, header_font, detail_font, max_width, draw)
        if total <= max_height:
            best_size = mid
            low = mid + 1
        else:
            high = mid - 1

    return best_size


def generate_forecast_image(config, forecast_data, output_path=None):
    """
    Generate an image displaying the detailed forecast.
    Auto-sizes text to be as large as possible while fitting on the display.
    """
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

    # Create image
    img = Image.new("RGB", (width, height), color=background_color)
    draw = ImageDraw.Draw(img)

    max_text_width = width - 2 * margin
    available_height = height - 2 * margin

    # Build forecast blocks
    blocks = build_forecast_blocks(forecast_data, num_periods)

    if not blocks:
        print("No forecast periods available")
        img.save(output_path)
        return output_path

    # Header scale factor (period name is this much bigger than detail text)
    header_scale = forecast_cfg.get("header_scale", 1.3)

    # Find the largest detail font size that fits
    best_size = find_best_font_size(
        blocks, font_path, max_text_width, available_height,
        draw, max_font_size, min_font_size, header_scale
    )
    header_size = int(best_size * header_scale)
    print(f"Auto-selected font size: {best_size} (period names: {header_size}, max allowed: {max_font_size})")

    try:
        detail_font = ImageFont.truetype(font_path, best_size)
        header_font = ImageFont.truetype(font_path, header_size)
    except:
        print("Could not load custom font, using default")
        detail_font = ImageFont.load_default()
        header_font = detail_font

    # Draw all forecast blocks
    y_offset = margin
    line_spacing = 2
    section_spacing = 8

    # Get font metrics for baseline alignment on header lines only
    header_ascent, header_descent = header_font.getmetrics()
    detail_ascent, detail_descent = detail_font.getmetrics()

    # Consistent detail line height
    sample_bbox = draw.textbbox((0, 0), "Ag", font=detail_font)
    detail_line_h = sample_bbox[3] - sample_bbox[1]

    for block in blocks:
        # Draw "Name:" in big font
        name_with_colon = block['name'] + ":"
        name_bbox = draw.textbbox((0, 0), name_with_colon, font=header_font)
        name_w = name_bbox[2] - name_bbox[0]
        name_h = name_bbox[3] - name_bbox[1]

        # Measure subtitle in normal font
        sub_bbox = draw.textbbox((0, 0), block['subtitle'], font=detail_font)
        sub_w = sub_bbox[2] - sub_bbox[0]
        sub_h = sub_bbox[3] - sub_bbox[1]

        header_line_h = max(name_h, sub_h)

        # Align subtitle to bottom of header text using font metrics
        name_y = y_offset
        sub_y = y_offset + (header_ascent - detail_ascent)

        draw.text((margin, name_y), name_with_colon, fill=text_color, font=header_font)

        if name_w + sub_w <= max_text_width:
            # Subtitle fits on same line
            draw.text((margin + name_w, sub_y), block['subtitle'], fill=text_color, font=detail_font)
            y_offset += header_line_h + line_spacing
        else:
            # Subtitle wraps to next line
            y_offset += header_line_h + line_spacing
            wrapped_sub = wrap_text(block['subtitle'].strip(), detail_font, max_text_width, draw)
            for line in wrapped_sub:
                draw.text((margin, y_offset), line, fill=text_color, font=detail_font)
                y_offset += detail_line_h + line_spacing

        # Draw detail text in normal font
        if block['detail']:
            wrapped = wrap_text(block['detail'], detail_font, max_text_width, draw)
            for line in wrapped:
                draw.text((margin, y_offset), line, fill=text_color, font=detail_font)
                y_offset += detail_line_h + line_spacing

        y_offset += section_spacing

    # Draw location in top right corner
    location = forecast_data.get('location', '')
    if location:
        loc_bbox = draw.textbbox((0, 0), location, font=detail_font)
        loc_w = loc_bbox[2] - loc_bbox[0]
        draw.text((width - loc_w - margin, margin), location, fill=text_color, font=detail_font)

    # Save image
    img.save(output_path)
    print(f"Forecast image saved to: {output_path}")

    return output_path


def main():
    """Main function to fetch forecast and generate display image."""
    config = load_config('config.yml')

    # Get coordinates from config
    forecast_config = config.get('forecast_location', {})
    lat = forecast_config.get('latitude')
    lon = forecast_config.get('longitude')

    if not lat or not lon:
        print("Error: latitude and longitude not found in config.yml")
        print("Please add forecast_location section with latitude and longitude")
        return

    # Fetch forecast data
    forecast_data = get_detailed_forecast(lat, lon)

    if not forecast_data:
        print("Failed to fetch forecast data")
        return

    # Generate image
    output_path = "forecast_display.bmp"
    image_path = generate_forecast_image(config, forecast_data, output_path)

    if image_path and platform.system() == "Linux":
        # Display on Waveshare e-ink (only on Raspberry Pi)
        try:
            display_color_image(image_path)
            print(f"Displayed forecast on e-ink display")
        except Exception as e:
            print(f"Error displaying image: {e}")
    elif image_path:
        print(f"Image generated successfully: {image_path}")
        print(f"(Display update skipped - not running on Raspberry Pi)")


if __name__ == "__main__":
    main()
