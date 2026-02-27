import time
import json
import requests
import io
from PIL import Image, ImageDraw, ImageFont
import qrcode
from special_weather_message import get_special_weather_messages
from eink_generator import load_config  # assuming load_config loads your YAML config
from detailed_forecast import get_detailed_forecast, generate_forecast_image
import math
import platform
import os

try:
    from modules.weather import fetch_current_conditions, draw_conditions_panel
    _PANEL_SUPPORT = True
except ImportError:
    _PANEL_SUPPORT = False

if platform.system() == "Linux":  # Only import on Raspberry Pi
    # from waveshare_epd import epd7in5_V2, epd7in3f  # Adjust the import based on your specific model
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
    # Euclidean distance in RGB space
    return math.sqrt((c1[0] - c2[0])**2 + (c1[1] - c2[1])**2 + (c1[2] - c2[2])**2)

def quantize_to_seven_colors(input_path, output_path, more_colors, threshold=0):
    """
    Quantize an image to 7 colors:
      - Pixels within a Euclidean distance 'threshold' of white (255,255,255) are set to white.
      - All other pixels are mapped to the closest color from a fixed five-color palette.
    """
    white = (255, 255, 255)

    if more_colors:
        palette_5 = [
        # Red steps
        (255, 204, 204),  # Light Red
        (255, 102, 102),  # Medium Light Red
        (255, 0, 0),      # Red
        (153, 0, 0),      # Dark Red
        (102, 0, 0),      # Very Dark Red

        # Green steps
        (204, 255, 204),  # Light Green
        (102, 255, 102),  # Medium Light Green
        (0, 255, 0),      # Green
        (0, 153, 0),      # Dark Green
        (0, 102, 0),      # Very Dark Green

        # Blue steps
        (204, 204, 255),  # Light Blue
        (102, 102, 255),  # Medium Light Blue
        (0, 0, 255),      # Blue
        (0, 0, 153),      # Dark Blue
        (0, 0, 102),      # Very Dark Blue

        # Yellow steps
        (255, 255, 204),  # Light Yellow
        (255, 255, 102),  # Medium Light Yellow
        (255, 255, 0),    # Yellow
        (204, 204, 0),    # Dark Yellow
        (153, 153, 0),    # Very Dark Yellow

        # Orange steps
        (255, 224, 192),  # Light Orange
        (255, 178, 102),  # Medium Light Orange
        (255, 128, 0),    # Orange
        (204, 102, 0),    # Dark Orange
        (153, 76, 0),     # Very Dark Orange

        # Black and white
        (0, 0, 0),        # Black

    ]
    else: 
        palette_5 = [
        (255, 0, 0),   # red
        (0, 255, 0),   # green
        (0, 0, 255),   # blue
        (255, 255, 0), # yellow
        (255, 128, 0), # orange
        (0, 0, 0)     # black
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
    """
    Generate a weather image from the National Weather Service radar for the given station.
    Saves the image (and its quantized version) into the "radar" folder.
    Returns a tuple (output_path, updated) where updated is False if the generated image is identical.
    """
    radar_folder = "radar"
    os.makedirs(radar_folder, exist_ok=True)
    
    width = config.get("width", 800)
    height = config.get("height", 480)
    background_color = config.get("background_color_weather", "white")
    station = config.get("station", {}).get("name", "KTYX")

    output_path = config.get("output_path") or os.path.join(radar_folder, f"eink_display_{station}.bmp")
    quantized_output_path = config.get("quantized_path") or os.path.join(radar_folder, f"eink_quantized_display_{station}.bmp")

    radar_mode = config.get("radar_mode", "crop").lower()
    final_img = Image.new("RGB", (width, height), color=background_color)

    radar_url = f"https://radar.weather.gov/ridge/standard/{station}_0.gif"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    # Retry mechanism for fetching the radar image
    for attempt in range(3):
        response = requests.get(radar_url, headers=headers)
        if response.status_code == 200:
            break
        elif response.status_code == 404 and attempt < 2:
            print(f"Image not found (404). Retrying in 30 seconds... (Attempt {attempt + 1})")
            time.sleep(2)
        else:
            print(f"Failed to fetch image. Status code: {response.status_code}")
            return None  # Stop execution

    content_type = response.headers.get("Content-Type", "")
    if "image" not in content_type:
        print(f"Unexpected content type: {content_type}")
        print(f"Response content (first 500 bytes): {response.content[:500]}")
        return None

    # Try opening the image
    radar_img = Image.open(io.BytesIO(response.content)).convert("RGB")
    
    if radar_mode == "crop":
        scale = max(width / radar_img.width, height / radar_img.height)
        new_w = int(radar_img.width * scale)
        new_h = int(radar_img.height * scale)
        scaled_radar = radar_img.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - width) // 2
        top = (new_h - height) // 2
        processed_radar = scaled_radar.crop((left, top, left + width, top + height))
    elif radar_mode == "fit":
        scale = min(width / radar_img.width, height / radar_img.height)
        new_w = int(radar_img.width * scale)
        new_h = int(radar_img.height * scale)
        processed_radar = radar_img.resize((new_w, new_h), Image.LANCZOS)
        x_offset = (width - new_w) // 2
        y_offset = (height - new_h) // 2
        final_img.paste(processed_radar, (x_offset, y_offset))
        processed_radar = None
    elif radar_mode == "panel":
        if not _PANEL_SUPPORT:
            print("[warning] Panel mode requires modules/weather.py — falling back to crop")
            scale = max(width / radar_img.width, height / radar_img.height)
            new_w = int(radar_img.width * scale)
            new_h = int(radar_img.height * scale)
            scaled_radar = radar_img.resize((new_w, new_h), Image.LANCZOS)
            left = (new_w - width) // 2
            top = (new_h - height) // 2
            processed_radar = scaled_radar.crop((left, top, left + width, top + height))
        else:
            from datetime import date as _date
            panel_w = config.get("panel_width", 280)
            radar_w = width - panel_w
            header_h = 30

            # Strip NWS title bar + legend; scale radar below the header bar
            data_radar = radar_img.crop((0, 38, radar_img.width, radar_img.height - 24))
            radar_canvas_h = height - header_h
            scale = max(radar_w / data_radar.width, radar_canvas_h / data_radar.height)
            rw = int(data_radar.width * scale)
            rh = int(data_radar.height * scale)
            scaled_radar = data_radar.resize((rw, rh), Image.LANCZOS)
            left_crop = (rw - radar_w) // 2
            top_crop  = (rh - radar_canvas_h) // 2
            processed_radar = scaled_radar.crop((left_crop, top_crop,
                                                 left_crop + radar_w, top_crop + radar_canvas_h))
            final_img.paste(processed_radar, (0, header_h))

            draw_tmp = ImageDraw.Draw(final_img)
            draw_tmp.rectangle([(radar_w, header_h), (width - 1, height - 1)], fill="white")

            # Fetch conditions and draw panel content
            forecast_loc = config.get("forecast_location", {})
            lat = forecast_loc.get("latitude")
            lon = forecast_loc.get("longitude")
            conditions = fetch_current_conditions(lat, lon, headers) if lat and lon else None
            draw_conditions_panel(final_img, conditions, config, radar_w, panel_w, header_h=header_h)

            # Snap panel (below header only) to pure B/W before drawing header text
            panel_box = (radar_w, header_h, width, height)
            panel_bw = final_img.crop(panel_box).convert("L").point(
                lambda px: 255 if px > 128 else 0
            ).convert("RGB")
            final_img.paste(panel_bw, (radar_w, header_h))

            # Draw header bar LAST so snap doesn't erode the white-on-black text
            draw_tmp = ImageDraw.Draw(final_img)
            draw_tmp.rectangle([(0, 0), (width - 1, header_h - 1)], fill=(0, 0, 0))
            draw_tmp.line([(radar_w, header_h), (radar_w, height - 1)], fill=(180, 180, 180), width=1)

            loc_name = config.get("panel_header") or forecast_loc.get("name", "")
            weekday = _date.today().strftime("%a")
            hdr_str = f"{loc_name}  {weekday}" if loc_name else weekday
            hdr_avail_h = header_h - 6
            hdr_text_w = panel_w - 16
            hdr_font = None
            font_path = config.get("font_path", "")
            for path in [
                config.get("bold_font_path", ""),
                "/System/Library/Fonts/Supplemental/DIN Alternate Bold.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                font_path,
            ]:
                if not path:
                    continue
                for size in range(20, 9, -1):
                    try:
                        f = ImageFont.truetype(path, size)
                        bb = draw_tmp.textbbox((0, 0), hdr_str, font=f)
                        if (bb[2] - bb[0]) <= hdr_text_w and (bb[3] - bb[1]) <= hdr_avail_h:
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

            processed_radar = None
    else:
        raise ValueError(f"Invalid radar_mode '{radar_mode}'. Use 'crop', 'fit', or 'panel'.")

    if processed_radar is not None:
        final_img.paste(processed_radar, (0, 0))

    # If a quantized image already exists, load it for later comparison.
    old_quant = None
    if os.path.exists(quantized_output_path):
        old_quant = Image.open(quantized_output_path).convert("RGB")
    
    # Overlay special weather alert QR code (top-right of the radar area, never on the panel)
    if config.get("check_special_weather", True) and special_msg:
        try:
            special_url = config.get('special_url', "https://forecast.weather.gov/showsigwx.php?warnzone=TNZ027&warncounty=TNC037&firewxzone=TNZ027&local_place1=Nashville%20TN")
            qr_size = 138
            qr_alert = qrcode.make(special_url).resize((qr_size, qr_size), Image.LANCZOS)
            # In panel mode keep QR inside the radar portion; otherwise use full width
            panel_w = config.get("panel_width", 280) if radar_mode == "panel" else 0
            radar_right = width - panel_w
            qr_x = radar_right - qr_size - 2
            final_img.paste(qr_alert, (qr_x, 2))
        except Exception as e:
            print(f"Error adding special weather QR code: {e}")

    final_img.save(output_path)
    print(f"Saved final weather image to {output_path}")
    # Generate new quantized image from the updated raw image.

    more_colors = config.get('more_colors', False)
    quantize_to_seven_colors(output_path, quantized_output_path, more_colors, threshold=75)
    new_quant = Image.open(quantized_output_path).convert("RGB")
    if old_quant is not None and images_are_equal(old_quant, new_quant):
        print(f"Station {station}: Quantized image unchanged.")
        return None, False
    return quantized_output_path, True

def calculate_non_bw_percentage(image_path):
    """
    Calculate the percentage of pixels that are not pure black or white.
    """
    image = Image.open(image_path).convert("RGB")
    pixels = list(image.getdata())
    if not pixels:
        return 0.0
    non_bw_count = sum(1 for pixel in pixels if pixel not in [(0, 0, 0), (255, 255, 255)])
    return (non_bw_count / len(pixels)) * 100

def full_station_scan(config):
    """
    Perform a full scan over all stations from config.
    Returns a dictionary mapping station -> interesting pixel percentage.
    """
    percentages = {}
    special_msg = get_special_weather_messages()
    for station_data in config.get("stations", []):
        station = station_data.get("name")
        if not station:
            continue
        config["station"] = station_data
        config["output_path"] = os.path.join("radar", f"eink_display_{station}.bmp")
        config["quantized_path"] = os.path.join("radar", f"eink_quantized_display_{station}.bmp")
        result = generate_weather_image(config, special_msg=special_msg)
        if result is None:
            print(f"Skipping processing for station {station} due to image fetch failure.")
            continue
        perc = calculate_non_bw_percentage(config["quantized_path"])
        percentages[station] = perc
        print(f"Full scan: Station {station} -> {perc:.2f}% interesting pixels")
    return percentages

def update_top5(percentages):
    """
    Return a sorted list (descending by percentage) of the top 5 stations.
    """
    sorted_stations = sorted(percentages.items(), key=lambda x: x[1], reverse=True)
    top5 = sorted_stations[:5]
    return top5


def main():
    radar_folder = "radar"
    os.makedirs(radar_folder, exist_ok=True)
    
    config = load_config('config.yml')
    special_msg = get_special_weather_messages()

    # Load persistent state, which stores the time of the last full scan and the cached top5.
    state = load_state(STATE_FILE) or {}
    now = time.time()
    full_scan_interval = config.get('full_scan_interval', 3600)  # one hour in seconds

    # Use cached top5 data if available, regardless of age.
    top5_data = state.get("top5", [])
    if top5_data:
        top5_list = [(item["station"], item["percentage"]) for item in top5_data]
        config["top5"] = top5_list
    else:
        top5_list = []  # will be updated later if needed

    # Process the default station.
    default_station = config.get("station", {}).get("name", "KTYX")
    config["output_path"] = os.path.join(radar_folder, f"eink_display_{default_station}.bmp")
    config["quantized_path"] = os.path.join(radar_folder, f"eink_quantized_display_{default_station}.bmp")
    default_image_path, default_updated = generate_weather_image(config, special_msg=special_msg)
    if default_image_path is None and not default_updated:
        print(f"Default station {default_station}: No changes detected. Keeping current display.")
        default_image_path = config["quantized_path"]
    default_percentage = calculate_non_bw_percentage(config["quantized_path"])
    print(f"Default station ({default_station}) has {default_percentage:.2f}% interesting pixels.")
    should_update = default_updated  # Use a flag to indicate if an update occurred
    final_display_image = default_image_path

    
    # Save updated state (including last_ten) so it carries over on refresh
    save_state(STATE_FILE, state)

    # If the default is below threshold and we have top5 data (or want to check a smaller subset)
    if default_updated and default_percentage < config.get('interesting_threshold', 15) and top5_list:
        best_station = None
        best_percentage = 0
        best_image_path = None
        for station, _ in top5_list:
            config["station"] = {"name": station}
            config["output_path"] = os.path.join(radar_folder, f"eink_display_{station}.bmp")
            config["quantized_path"] = os.path.join(radar_folder, f"eink_quantized_display_{station}.bmp")
            station_entry = next((s for s in config.get("stations", []) if s["name"] == station), {})
            config['station']['location'] = station_entry.get("location", "Unknown Location")            
            image_path, updated = generate_weather_image(config, special_msg=special_msg)
            if image_path is None:
                print(f"Skipping processing for station {station} due to image fetch failure.")
                continue
            should_update = True  # Set flag to True for any successful image generation
            perc = calculate_non_bw_percentage(config["quantized_path"])
            if perc > best_percentage:
                best_percentage = perc
                best_station = station
                best_image_path = config["quantized_path"]
        if best_station and best_image_path is not None:
            print(f"Switching display to station {best_station} with {best_percentage:.2f}% interesting pixels.")
            final_display_image = best_image_path
            current_station = best_station
    else:
        print("Default station is dynamic enough; using default image.")
    
    # If no interesting radar found anywhere, fall back to forecast display
    if 'best_percentage' not in locals():
        best_percentage = default_percentage
    final_percentage = best_percentage if 'best_station' in locals() and best_station else default_percentage

    if config.get('show_forecast_fallback', False) and final_percentage < config.get('interesting_threshold', 15):
        print(f"No interesting radar found (best: {final_percentage:.2f}%). Falling back to forecast display.")
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
                    print(f"Using forecast display: {forecast_path}")
            else:
                print("Failed to fetch forecast data, keeping radar display.")
        else:
            print("No forecast_location coordinates in config, keeping radar display.")

    if 'current_station' not in locals():
        current_station = default_station
    
    last_ten = state.get("last_ten", [])
    # if current_station in last_ten:
    #     last_ten.remove(current_station)
    last_ten.append(current_station)
    if len(last_ten) > 10:
        last_ten = last_ten[-10:]
    state["last_ten"] = last_ten
    config["last_ten"] = last_ten
    save_state(STATE_FILE, state)

    if should_update:  # Check if an update occurred
        # Update the final display image to include the updated last_ten overlay
        try:
            last_ten = state.get("last_ten", [])
            from PIL import ImageDraw, ImageFont
            final_img = Image.open(final_display_image).convert("RGB")
            draw = ImageDraw.Draw(final_img)
            font = ImageFont.load_default()
            margin = 10
            left_margin = margin
            count = len(last_ten)
            if count > 0:
                sample_text = "Sample"
                bbox_sample = draw.textbbox((0, 0), sample_text, font=font)
                line_height = bbox_sample[3] - bbox_sample[1]
                total_text_height = count * (line_height + margin) - margin
                bottom_y = final_img.height - margin - total_text_height
                for i, station in enumerate(last_ten):
                    text_str = f"{station}"
                    draw.text((left_margin, bottom_y + i * (line_height + margin)), text_str, fill="black", font=font)
            final_img.save(final_display_image)
        except Exception as e:
            print(f"Failed to update last_ten overlay: {e}")

        if platform.system() == "Linux":  # Only display on Raspberry Pi
            display_color_image(final_display_image)
            print(f"Displayed image: {final_display_image}")
        else:
            print(f"Skipping display update on non-Raspberry Pi system: {platform.system()}")

        # Check if it's time for a full refresh.
        if now - state.get("last_full_scan", 0) >= full_scan_interval:
            # Immediately mark the state as updated before starting the full scan.
            state["last_full_scan"] = now
            state["top5"] = state.get('top5', [])
            state["last_ten"] =  state.get('last_ten', [])
            save_state(STATE_FILE, state)
            print("Full refresh state updated in JSON. Running full refresh after display update...")
            
            percentages = full_station_scan(config)
            top5_list = update_top5(percentages)
            top5_data = [{"station": s, "percentage": p} for s, p in top5_list]
            state["top5"] = top5_data
            save_state(STATE_FILE, state)
        else:
            print("Not enough time has elapsed for full refresh.")
    else:
        print("No image changes detected; display update skipped.")

if __name__ == '__main__':
    main()