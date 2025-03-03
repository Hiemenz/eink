import requests
import io
from PIL import Image
from eink_generator import load_config  # assuming load_config loads your YAML config
from display import display_single_image
import os

def images_are_equal(img1, img2):
    if img1.mode != img2.mode or img1.size != img2.size:
        return False
    return list(img1.getdata()) == list(img2.getdata())

def generate_weather_image(config):
    """
    Generate a weather image of size (width x height) with a radar image
    from the National Weather Service. The radar image is either "cropped"
    (covering the entire canvas with center-cropping) or "fitted" (centered
    on a background) based on the config parameter "radar_mode".
    """
    width = config.get("width", 800)
    height = config.get("height", 480)
    background_color = config.get("background_color_weather", "white")
    station = config.get("station", "KTYX")
    radar_mode = config.get("radar_mode", "crop").lower()  # "crop" or "fit"
    output_path = config.get("output_path", "eink_display.bmp")
    output_mode = config.get("output_mode", "color")
    # Create the final canvas with the configurable background color.
    final_img = Image.new("RGB", (width, height), color=background_color)

    # Download the radar image.
    radar_url = f"https://radar.weather.gov/ridge/standard/{station}_0.gif"

    # radar_url = 'https://www.wpc.ncep.noaa.gov/noaa/noaad1.gif'

    response = requests.get(radar_url)
    radar_img = Image.open(io.BytesIO(response.content)).convert("RGB")

    if radar_mode == "crop":
        # "Crop" mode: Scale so that the image covers the entire canvas, then center-crop.
        scale_x = width / radar_img.width
        scale_y = height / radar_img.height
        scale = max(scale_x, scale_y)  # scale up so both dimensions cover the canvas
        new_w = int(radar_img.width * scale)
        new_h = int(radar_img.height * scale)
        scaled_radar = radar_img.resize((new_w, new_h), Image.LANCZOS)

        # Calculate coordinates to center-crop the image.
        left = (new_w - width) // 2
        top = (new_h - height) // 2
        right = left + width
        bottom = top + height
        processed_radar = scaled_radar.crop((left, top, right, bottom))

    elif radar_mode == "fit":
        # "Fit" mode: Scale so that the entire image fits inside the canvas.
        scale_x = width / radar_img.width
        scale_y = height / radar_img.height
        scale = min(scale_x, scale_y)  # scale down so that the entire image fits
        new_w = int(radar_img.width * scale)
        new_h = int(radar_img.height * scale)
        processed_radar = radar_img.resize((new_w, new_h), Image.LANCZOS)

        # Calculate the position to paste the image centered.
        x_offset = (width - new_w) // 2
        y_offset = (height - new_h) // 2

        # Paste the fitted image onto the canvas.
        final_img.paste(processed_radar, (x_offset, y_offset))
        processed_radar = None  # we already pasted it
    else:
        raise ValueError(f"Invalid radar_mode '{radar_mode}'. Use 'crop' or 'fit'.")

    # For "crop" mode, paste the processed radar image onto the final canvas.
    if processed_radar is not None:
        final_img.paste(processed_radar, (0, 0))

    # (Optional) Add any additional drawing or text here.
    # draw = ImageDraw.Draw(final_img)
    # draw.text((10,10), "Weather Update", fill="black")

    output_mode = config.get("output_mode", "color").lower()

    if output_mode == "binary":
        final_img = final_img.convert("1")
    elif output_mode == "grayscale":
        final_img = final_img.convert("L")
    elif output_mode == "color":
        # No conversion needed; keep the image in full color.
        pass
    else:
        raise ValueError(f"Invalid output_mode '{output_mode}'. Use 'color', 'grayscale', or 'binary'.")
    


    if os.path.exists(output_path):
        # Convert the loaded image to the same mode as final_img
        existing_img = Image.open(output_path).convert(output_mode)
        if images_are_equal(existing_img, final_img):
            print('images the same do nothing')
            return
    

    final_img.save(output_path)
    print(f"Saved final weather image to {output_path}")

    print('displaying image')
    display_single_image(output_path)
    return final_img

def main():
    config = load_config('config.yml')
    generate_weather_image(config)

if __name__ == '__main__':
    main()