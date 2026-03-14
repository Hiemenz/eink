import requests
import json
import yaml
import csv
import random
import uuid
from PIL import Image, ImageDraw, ImageFont
import platform
import os

if platform.system() != "Darwin":
    from display import display_single_image


def wrap_text(text, font, draw, max_width):
    words = text.split()
    lines = []
    current_line = words[0] if words else ""
    for word in words[1:]:
        test_line = current_line + " " + word
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if bbox[2] - bbox[0] <= max_width - 20:
            current_line = test_line
        else:
            lines.append(current_line)
            current_line = word
    lines.append(current_line)
    return "\n".join(lines)


def generate_image(text, width, height, image_path):
    if platform.system() == "Darwin":
        font_path = "/Library/Fonts/Arial.ttf"
    elif platform.system() == "Linux":
        font_path = '/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf'
    else:
        raise RuntimeError("Unsupported operating system")

    font_size = 60
    img = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    while True:
        font = ImageFont.truetype(font_path, font_size)
        wrapped_text = wrap_text(text, font, draw, width)
        bbox = draw.multiline_textbbox((0, 0), wrapped_text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        if text_width < width - 20 and text_height < height - 20:
            break
        font_size -= 2
        if font_size < 20:
            break

    x = (width - text_width) // 2
    y = (height - text_height) // 2
    draw.multiline_text((x, y), wrapped_text, fill="black", font=font, align="center")
    img.save(image_path)
    return image_path


def get_random_question(csv_path):
    with open(csv_path, newline='', encoding='utf-8') as csvfile:
        rows = list(csv.DictReader(csvfile))
    if not rows:
        raise ValueError("CSV file is empty or malformed.")
    return random.choice(rows)["question"].strip()


def generate_content(prompt, api_key=None):
    if not api_key:
        raise ValueError("API key not found.")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    data = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 1.8}
    }

    response = requests.post(url, headers=headers, data=json.dumps(data))
    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(f"Error: {response.status_code}, {response.text}")


def generate(config):
    """Module interface: generate text/fact image and return output path."""
    text_cfg = config.get('text', {})
    width = config.get('width', 800)
    height = config.get('height', 480)
    image_path = text_cfg.get('output_path', 'images/text_display.bmp')

    # Message set directly via Discord (!text command) takes priority
    direct_message = text_cfg.get('message', '').strip()
    if direct_message:
        return generate_image(direct_message, width, height, image_path)

    # Fall back to display_text_config.yml for AI/CSV-driven content
    text_config_path = config.get('text_config', 'display_text_config.yml')
    with open(text_config_path, 'r') as f:
        text_config = yaml.safe_load(f)

    if text_config.get('override_message_trigger', False):
        text_content = text_config.get('override_message', '')
    elif text_config.get('csv_question_file'):
        text_content = get_random_question(text_config['csv_question_file'])
    else:
        prompt = str(uuid.uuid4()) + ' ' + text_config['instructions']
        result = generate_content(prompt, text_config['GEMINI_API_KEY'])
        text_content = result["candidates"][0]["content"]["parts"][0]["text"]

    print(text_content)
    width = text_config.get('width', width)
    height = text_config.get('height', height)
    image_path = text_config.get('image_path', image_path)
    return generate_image(text_content, width, height, image_path)


def main():
    try:
        with open('display_text_config.yml', 'r') as f:
            import yaml
            text_config = yaml.safe_load(f)

        if text_config.get('override_message_trigger', False):
            text_content = text_config.get('override_message', '')
        elif text_config.get('csv_question_file'):
            text_content = get_random_question(text_config['csv_question_file'])
        else:
            prompt = str(uuid.uuid4()) + ' ' + text_config['instructions']
            result = generate_content(prompt, text_config['GEMINI_API_KEY'])
            text_content = result["candidates"][0]["content"]["parts"][0]["text"]

        print(text_content)
        width = text_config.get('width', 800)
        height = text_config.get('height', 480)
        image_path = text_config.get('image_path', 'test_image.bmp')
        generate_image(text_content, width, height, image_path)

        if platform.system() != "Darwin":
            display_single_image(image_path)
        else:
            print('skipping display')

    except Exception as e:
        print(e)


if __name__ == "__main__":
    main()
