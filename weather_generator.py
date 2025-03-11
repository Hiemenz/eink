import requests
import io
from PIL import Image
from eink_generator import load_config  # assuming load_config loads your YAML config
import os
import math

from display import display_color_image

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
    
    # Fixed five-color palette plus black.
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
            # If near white, set to white.
            if distance(p, white) <= threshold:
                pixels[x, y] = white
            else:
                best_color = None
                best_dist = float("inf")
                for color in palette_5:
                    d = distance(p, color)
                    if d < best_dist:
                        best_dist = d
                        best_color = color
                pixels[x, y] = best_color

    original.save(output_path, format="bmp")
    print(f"Quantized image saved to {output_path}")

def generate_weather_image(config):
    """
    Generate a weather image using a radar image from the National Weather Service.
    Saves the final image into the "radar" folder with a station-specific filename.
    Returns a tuple (output_path, updated) where 'updated' is True if a new image was generated.
    """
    # Ensure the "radar" folder exists.
    radar_folder = "radar"
    os.makedirs(radar_folder, exist_ok=True)
    
    width = config.get("width", 800)
    height = config.get("height", 480)
    background_color = config.get("background_color_weather", "white")
    station = config.get("station", "KTYX")
    
    # Build station-specific output filenames in the "radar" folder.
    output_path = config.get("output_path") or os.path.join(radar_folder, f"eink_display_{station}.bmp")
    quantized_output_path = config.get("quantized_path") or os.path.join(radar_folder, f"eink_quantized_display_{station}.bmp")
    
    radar_mode = config.get("radar_mode", "crop").lower()  # "crop" or "fit"
    
    # Create the final canvas.
    final_img = Image.new("RGB", (width, height), color=background_color)
    
    # Download the radar image.
    radar_url = f"https://radar.weather.gov/ridge/standard/{station}_0.gif"
    response = requests.get(radar_url)
    radar_img = Image.open(io.BytesIO(response.content)).convert("RGB")
    
    if radar_mode == "crop":
        scale_x = width / radar_img.width
        scale_y = height / radar_img.height
        scale = max(scale_x, scale_y)
        new_w = int(radar_img.width * scale)
        new_h = int(radar_img.height * scale)
        scaled_radar = radar_img.resize((new_w, new_h), Image.LANCZOS)
        
        left = (new_w - width) // 2
        top = (new_h - height) // 2
        right = left + width
        bottom = top + height
        processed_radar = scaled_radar.crop((left, top, right, bottom))
    elif radar_mode == "fit":
        scale_x = width / radar_img.width
        scale_y = height / radar_img.height
        scale = min(scale_x, scale_y)
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
    
    # If an image already exists, check if it is identical.
    if os.path.exists(output_path):
        existing_img = Image.open(output_path).convert(final_img.mode)
        if images_are_equal(existing_img, final_img):
            print('Default image is unchanged. No update needed.')
            return output_path, False
    
    final_img.save(output_path)
    print(f"Saved final weather image to {output_path}")
    
    # Quantize the final image to seven colors.
    quantize_to_seven_colors(output_path, quantized_output_path, threshold=75)
    
    print('Processing complete.')
    return output_path, True

def calculate_non_bw_percentage(image_path):
    """
    Calculate the percentage of pixels in the image that are not pure black or white.
    """
    image = Image.open(image_path).convert("RGB")
    pixels = list(image.getdata())
    total_pixels = len(pixels)
    if total_pixels == 0:
        return 0.0
    non_bw_count = sum(1 for pixel in pixels if pixel != (0, 0, 0) and pixel != (255, 255, 255))
    return (non_bw_count / total_pixels) * 100

def process_all_stations(config):
    """
    Iterate through all stations in the config, generating and analyzing each radar image,
    and return the station with the highest percentage of non-black/white pixels.
    """
    stations = config.get("stations", [])
    highest_percentage = 0
    best_station = None
    best_image_path = None

    for station in stations:
        print(f"\nProcessing station: {station}")
        config["station"] = station
        config["output_path"] = os.path.join("radar", f"eink_display_{station}.bmp")
        config["quantized_path"] = os.path.join("radar", f"eink_quantized_display_{station}.bmp")
        
        image_path, _ = generate_weather_image(config)
        percentage = calculate_non_bw_percentage(config["quantized_path"])
        print(f"Station {station} has {percentage:.2f}% non-black/white pixels.")
        
        if percentage > highest_percentage:
            highest_percentage = percentage
            best_station = station
            best_image_path = config["quantized_path"]
    
    if best_station:
        print(f"\nThe station with the highest non-black/white pixel percentage is {best_station} ({highest_percentage:.2f}%).")
    return best_station, highest_percentage, best_image_path

def main():
    # Ensure the "radar" folder exists.
    os.makedirs("radar", exist_ok=True)
    
    config = load_config('config.yml')
    station = config.get("station", "KTYX")
    config["output_path"] = os.path.join("radar", f"eink_display_{station}.bmp")
    config["quantized_path"] = os.path.join("radar", f"eink_quantized_display_{station}.bmp")
    
    # Process the default station and check if its image was updated.
    default_image_path, updated = generate_weather_image(config)
    
    if not updated:
        # Default image hasn't changed so skip retrieving other stations.
        print("Default image is unchanged. Skipping retrieval of other stations.")
        final_display_image = default_image_path
    else:
        default_percentage = calculate_non_bw_percentage(config["quantized_path"])
        print(f"\nDefault station ({station}) has {default_percentage:.2f}% non-black/white pixels.")
        
        final_display_image = default_image_path
        if default_percentage < config.get("interesting_threshold", 15):
            print("\nDefault station has low precipitation. Searching for a more interesting station...")
            best_station, best_percentage, best_image_path = process_all_stations(config)
            if best_station and best_station != station:
                print(f"Switching display to station {best_station} with {best_percentage:.2f}% interesting pixels.")
                final_display_image = best_image_path
            else:
                print("No better station found; keeping the default station image.")
        else:
            print("Default station image is dynamic enough; no need to switch stations.")
    
    # Single display update (uncomment the display call in your real deployment).
    display_color_image(final_display_image)
    print(f"Final image to display: {final_display_image}")

if __name__ == '__main__':
    main()