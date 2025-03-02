#!/usr/bin/env python3
import os
import sys
import hashlib
from PIL import Image, ImageDraw, ImageFont
import yaml

def load_config(yaml_path="config.yml"):
    """Load image configuration from YAML."""
    with open(yaml_path, "r") as f:
        config = yaml.safe_load(f)
    return config

def generate_image_from_text(text, config):
    """Generate an image with multi-line text without cutting off the last line."""
    # Read image settings from config
    width = config.get("width", 800)
    height = config.get("height", 480)
    bg_color = config.get("background_color", "white")
    text_color = config.get("text_color", "black")
    max_font_size = config.get("max_font_size", 51)
    min_font_size = config.get("min_font_size", 10)
    font_path = config.get("font_path", "/Library/Fonts/Arial Unicode.ttf")
    
    # Margins and line spacing
    margin = 20
    line_spacing = 2

    # Create a blank image
    img = Image.new("RGB", (width, height), color=bg_color)
    draw = ImageDraw.Draw(img)
    
    def wrap_text(text, font, max_width):
        """Wrap text into lines so each line fits within max_width."""
        words = text.split()
        lines = []
        current_line = ""
        for word in words:
            test_line = (current_line + " " + word).strip()
            bbox = draw.textbbox((0, 0), test_line, font=font)
            line_width = bbox[2] - bbox[0]
            if line_width <= max_width:
                current_line = test_line
            else:
                lines.append(current_line)
                current_line = word
        if current_line:
            lines.append(current_line)
        return lines

    def fits_in_image(font, lines):
        """
        Check if the wrapped lines fit within the image’s width and height
        using font metrics for line height.
        """
        ascent, descent = font.getmetrics()
        line_height = ascent + descent

        # Compute the total height for all lines, including spacing
        total_text_height = len(lines) * line_height + (len(lines) - 1) * line_spacing

        # Check if total height fits
        if total_text_height > (height - 2 * margin):
            return False

        # Check each line's width
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            line_width = bbox[2] - bbox[0]
            if line_width > (width - 2 * margin):
                return False

        return True

    def find_largest_fitting_font():
        """
        Decrease font size from max_font_size to min_font_size until the text fits.
        Return the largest font that fits along with the wrapped lines.
        """
        nonlocal max_font_size
        while max_font_size >= min_font_size:
            font = ImageFont.truetype(font_path, max_font_size)
            lines = wrap_text(text, font, width - 2 * margin)
            if fits_in_image(font, lines):
                return font, lines
            max_font_size -= 1
        
        # If nothing fits above min_font_size, just use min_font_size
        font = ImageFont.truetype(font_path, min_font_size)
        lines = wrap_text(text, font, width - 2 * margin)
        return font, lines

    # Get the largest font and wrapped lines that fit
    font, lines = find_largest_fitting_font()

    # Use font metrics to determine line height
    ascent, descent = font.getmetrics()
    line_height = ascent + descent

    # Calculate total text block height
    total_text_height = len(lines) * line_height + (len(lines) - 1) * line_spacing

    # Compute the starting y-offset to center the text block
    y_offset = (height - total_text_height) // 2

    # Draw the lines
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_width = bbox[2] - bbox[0]
        x_offset = (width - line_width) // 2  # center horizontally
        draw.text((x_offset, y_offset), line, fill=text_color, font=font)
        y_offset += line_height + line_spacing

    return img

def images_are_equal(img1, img2):
    """Compare two images by hashing their byte content."""
    hash1 = hashlib.md5(img1.tobytes()).hexdigest()
    hash2 = hashlib.md5(img2.tobytes()).hexdigest()
    return hash1 == hash2

def update_eink_display(new_img, output_path="eink_display.bmp"):
    """
    Compare the new image to the current file.
    Only update (overwrite) if they are different.
    """
    if os.path.exists(output_path):
        try:
            current_img = Image.open(output_path)
            if images_are_equal(current_img, new_img):
                print("No update needed; images are identical.")
                return False
        except Exception as e:
            print("Error comparing images:", e)
    new_img.save(output_path)
    print("E‑ink display updated with new image.")
    return True

def main():
    if len(sys.argv) < 2:
        print("Usage: python eink_image_generator.py 'Your text here'")
        sys.exit(1)
    
    text = sys.argv[1]
    config = load_config("config.yml")
    new_img = generate_image_from_text(text, config)
    update_eink_display(new_img, config.get("output_path", "eink_display.bmp"))

if __name__ == "__main__":
    main()