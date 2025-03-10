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
    
    :param input_path: Path to the input image.
    :param output_path: Path to save the quantized image.
    :param threshold: Distance threshold to consider a pixel "close" to white.
                      Use 0 to require an exact match.
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
    Generate a weather image with a radar image from the National Weather Service.
    The radar image is either "cropped" (covering the entire canvas with center-cropping)
    or "fitted" (centered on a background) based on the config parameter "radar_mode".
    After generating the final image, quantize the image colors.
    """
    width = config.get("width", 800)
    height = config.get("height", 480)
    background_color = config.get("background_color_weather", "white")
    station = config.get("station", "KTYX")
    radar_mode = config.get("radar_mode", "crop").lower()  # "crop" or "fit"
    output_path = config.get("output_path", "eink_display.bmp")
    quantized_output_path = config.get("quantized_path","eink_quantized_display.bmp")
    
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
    
    if os.path.exists(output_path):
        existing_img = Image.open(output_path).convert(final_img.mode)
        if images_are_equal(existing_img, final_img):
            print('Images are the same. No update needed.')
            return
    
    final_img.save(output_path)
    print(f"Saved final weather image to {output_path}")
    
    # Quantize the final image to seven colors.
    
    quantize_to_seven_colors(output_path, quantized_output_path, threshold=75)
    
    print('Processing complete.')

    display_color_image(quantized_output_path)
    return output_path

def calculate_non_bw_percentage(image_path):
    image = Image.open(image_path)
    # Ensure the image is in RGB mode.
    image = image.convert("RGB")
    pixels = list(image.getdata())
    
    total_pixels = len(pixels)
    if total_pixels == 0:
        return 0.0

    # Count pixels that are neither black nor white.
    non_bw_count = sum(1 for pixel in pixels if pixel != (0, 0, 0) and pixel != (255, 255, 255))
    
    # Calculate the percentage.
    percentage = (non_bw_count / total_pixels) * 100
    return percentage


def main():
    config = load_config('config.yml')
    generate_weather_image(config)
    percentage = calculate_non_bw_percentage(config.get("quantized_path","eink_quantized_display.bmp"))
    print(percentage)

if __name__ == '__main__':
    main()