import time
import json
import requests
import io
from PIL import Image, ImageDraw, ImageFont
import qrcode
from eink_generator import load_config  # assuming load_config loads your YAML config
import math
import platform
import os

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

def quantize_to_seven_colors(input_path, output_path, threshold=0):
    """
    Quantize an image to 7 colors:
      - Pixels within a Euclidean distance 'threshold' of white (255,255,255) are set to white.
      - All other pixels are mapped to the closest color from a fixed five-color palette.
    """
    white = (255, 255, 255)
    palette_5 = [
        (255, 0, 0),   # red
        (0, 255, 0),   # green
        (0, 0, 255),   # blue
        (255, 255, 0), # yellow
        (255, 128, 0), # orange
        (0, 0, 0)      # black
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

def generate_weather_image(config):
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
    else:
        raise ValueError(f"Invalid radar_mode '{radar_mode}'. Use 'crop' or 'fit'.")

    if processed_radar is not None:
        final_img.paste(processed_radar, (0, 0))

    # If a quantized image already exists, load it for later comparison.
    old_quant = None
    if os.path.exists(quantized_output_path):
        old_quant = Image.open(quantized_output_path).convert("RGB")
    
    # Generate QR code for the radar image URL
    qr = qrcode.make(radar_url_qr)
    qr = qr.resize((138, 138), Image.LANCZOS)  # Resize QR code

    draw = ImageDraw.Draw(final_img)
    font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), location, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1] + 8
    margin = 0
    # Calculate positions so that QR code and text are overlaid in the bottom right corner
    qr_x = width - qr.width - margin + 2
    qr_y = height - (qr.height + text_height + 2 * margin)
    final_img.paste(qr, (qr_x, qr_y))

    # Draw text below the QR code
    text_x = qr_x
    text_y = qr_y + qr.height + margin
    draw.text((text_x, text_y), location, fill="black", font=font)
    
    # Draw top 5 stations on the left side if available
    top5 = config.get("top5", [])
    if top5 and config.get('show_top_5', True):
        margin = 10
        left_margin = margin
        top_margin = margin

        for i, (station, percentage) in enumerate(top5):
            text_str = f"{station}: {percentage:.1f}%"
            bbox_station = draw.textbbox((0, 0), text_str, font=font)
            line_height = bbox_station[3] - bbox_station[1]
            draw.text((left_margin, top_margin + i * (line_height + margin)), text_str, fill="black", font=font)

    final_img.save(output_path)
    print(f"Saved final weather image to {output_path}")
    # Generate new quantized image from the updated raw image.
    quantize_to_seven_colors(output_path, quantized_output_path, threshold=75)
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
    for station_data in config.get("stations", []):
        station = station_data.get("name")
        if not station:
            continue
        config["station"] = station_data
        config["output_path"] = os.path.join("radar", f"eink_display_{station}.bmp")
        config["quantized_path"] = os.path.join("radar", f"eink_quantized_display_{station}.bmp")
        result = generate_weather_image(config)
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
    print("Top 5 stations from full scan:", top5)
    return top5


def main():
    radar_folder = "radar"
    os.makedirs(radar_folder, exist_ok=True)
    
    config = load_config('config.yml')

    # Load persistent state, which stores the time of the last full scan and the cached top5.
    state = load_state(STATE_FILE) or {}
    now = time.time()
    full_scan_interval = config.get('full_scan_interval', 3600)  # one hour in seconds

    # Use cached top5 data if available and not expired.
    if state.get("last_full_scan") and (now - state["last_full_scan"] < full_scan_interval):
        top5_data = state.get("top5", [])
        top5_list = [(item["station"], item["percentage"]) for item in top5_data]
        print("Using cached top 5 stations:", top5_list)
        config["top5"] = top5_list  # Set top5 in config for use in generate_weather_image
    else:
        top5_list = []  # will be updated later if needed

    # Process the default station.
    default_station = config.get("station", {}).get("name", "KTYX")
    config["output_path"] = os.path.join(radar_folder, f"eink_display_{default_station}.bmp")
    config["quantized_path"] = os.path.join(radar_folder, f"eink_quantized_display_{default_station}.bmp")
    default_image_path, default_updated = generate_weather_image(config)
    if default_image_path is None and not default_updated:
        print(f"Default station {default_station}: No changes detected. Keeping current display.")
        default_image_path = config["quantized_path"]
    default_percentage = calculate_non_bw_percentage(config["quantized_path"])
    print(f"Default station ({default_station}) has {default_percentage:.2f}% interesting pixels.")
    should_update = default_updated  # Use a flag to indicate if an update occurred
    final_display_image = default_image_path
    

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
            image_path, updated = generate_weather_image(config)
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
    else:
        print("Default station is dynamic enough; using default image.")

    if should_update:  # Check if an update occurred
        if platform.system() == "Linux":  # Only display on Raspberry Pi
            display_color_image(final_display_image)
            print(f"Displayed image: {final_display_image}")
        else:
            print(f"Skipping display update on non-Raspberry Pi system: {platform.system()}")

        # Check if it's time for a full refresh.
        if now - state.get("last_full_scan", 0) >= full_scan_interval:
            # Immediately mark the state as updated before starting the full scan.
            state["last_full_scan"] = now
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