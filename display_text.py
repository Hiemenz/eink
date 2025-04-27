import requests
import json
import yaml
import csv
import random
import textwrap
from PIL import Image, ImageDraw, ImageFont
import platform

if platform.system() != "Darwin":
    from display import display_single_image 


def wrap_text(text, font, draw, max_width):
    words = text.split()
    lines = []
    current_line = words[0]
    for word in words[1:]:
        test_line = current_line + " " + word
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if bbox[2] - bbox[0] <= max_width - 20:  # leave a margin of 20 pixels
            current_line = test_line
        else:
            lines.append(current_line)
            current_line = word
    lines.append(current_line)
    return "\n".join(lines)

def generate_image(text, width, height, image_path):
    if platform.system() == "Darwin":
        # macOS
        font_path = "/Library/Fonts/Arial.ttf"
    elif platform.system() == "Linux":
        # Raspberry Pi (Linux)
        font_path =  '/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf'

    else:
        raise RuntimeError("Unsupported operating system")

    font_size = 60  # Start with a large size

    # Create a blank image
    img = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    # Dynamically shrink font size until wrapped text fits within the image
    while True:
        font = ImageFont.truetype(font_path, font_size)
        wrapped_text = wrap_text(text, font, draw, width)
        bbox = draw.multiline_textbbox((0, 0), wrapped_text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        if text_width < width - 20 and text_height < height - 20:
            break
        font_size -= 2  # Reduce font size if text overflows
        if font_size < 20:  # Minimum readable size
            break

    # Center text on the image
    x = (width - text_width) // 2
    y = (height - text_height) // 2
    draw.multiline_text((x, y), wrapped_text, fill="black", font=font, align="center")

    # Save image
    img.save(image_path)
    return image_path

def load_config(yaml_file="display_text/config.yml"):
    """Load the configuration from a YAML file and return the dictionary."""
    with open(yaml_file, "r") as file:
        config = yaml.safe_load(file)
    return config

def generate_content(prompt, api_key=None):

    if not api_key:
        raise ValueError("API key not found in YAML file.")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    data = {"contents": [{"parts": [{"text": prompt}]}],"generationConfig": {
        "temperature": 1.8  # You can tweak this between 0.0 and 2.0
    }}

    response = requests.post(url, headers=headers, data=json.dumps(data))

    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(f"Error: {response.status_code}, {response.text}")

def get_random_question(csv_path):
    """Return a random 'question' field from a CSV with headers 'topic,question'."""
    with open(csv_path, newline='', encoding='utf-8') as csvfile:
        rows = list(csv.DictReader(csvfile))
    if not rows:
        raise ValueError("CSV file is empty or malformed.")
    return random.choice(rows)["question"].strip()

def generate_seed():
    import uuid

    # Generate a random UUID
    random_uuid = uuid.uuid4()
    return str(random_uuid)

def main():

    try:
        config = load_config('display_text_config.yml')

        if config.get('override_message_trigger', False):
            text_content = config.get('override_message', 'this is a text holer')
        elif config.get('csv_question_file'):
            text_content = get_random_question(config['csv_question_file'])
        else:
            prompt = generate_seed() + ' ' + config['instructions']
            result = generate_content(prompt, config['GEMINI_API_KEY'])
            text_content = result["candidates"][0]["content"]["parts"][0]["text"]

        print(text_content)
        width = config.get('width', 800)
        height = config.get('height', 400)
        image_path = config.get('image_path', 'output.bmp')
        generate_image(text_content, width, height, image_path)
        # Only display the image on hardware that supports the e‑ink display
        if platform.system() != "Darwin":  # Skip display on macOS
            display_single_image(image_path)
        else: 
            print('skipping display')

    except Exception as e:
        print(e)
  
if __name__ == "__main__":

    main()
