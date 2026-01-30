#!/usr/bin/env python3
"""
Saint of the Day Generator for E-Ink Display

Displays the current feast day and saint of the day with a description
of what they did or why they are a saint.
"""

import os
import platform
import requests
import yaml
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

if platform.system() != "Darwin":
    from display import display_single_image


def load_config(yaml_path="saint_of_the_day_config.yml"):
    """Load configuration from YAML file."""
    with open(yaml_path, "r") as f:
        return yaml.safe_load(f)


def call_gemini_api(prompt, api_key, temperature=0.7):
    """Call the Gemini API to generate content."""
    if not api_key:
        raise ValueError("API key not provided.")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    data = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature}
    }

    response = requests.post(url, headers=headers, json=data, timeout=30)

    if response.status_code == 200:
        result = response.json()
        return result["candidates"][0]["content"]["parts"][0]["text"]
    else:
        raise RuntimeError(f"API Error: {response.status_code}, {response.text}")


def call_openai_api(prompt, api_key, model="gpt-3.5-turbo", temperature=0.7):
    """Call the OpenAI API to generate content."""
    if not api_key:
        raise ValueError("API key not provided.")

    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    data = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature
    }

    response = requests.post(url, headers=headers, json=data, timeout=30)

    if response.status_code == 200:
        result = response.json()
        return result["choices"][0]["message"]["content"]
    else:
        raise RuntimeError(f"API Error: {response.status_code}, {response.text}")


def get_saint_of_the_day(config):
    """Get the saint of the day using the configured API."""
    today = datetime.now()
    date_str = today.strftime("%B %d")

    prompt = f"""{config.get('prompt', f'''Today is {date_str}. Tell me about the Catholic saint whose feast day is celebrated today.

Include:
DATE: {date_str}

SAINT: The saint's name and title

FEAST DAY: The name of the feast day (if applicable)

LIFE: A brief description of who they were (2-3 sentences)

WHY A SAINT: What they did to become a saint or their significance (2-3 sentences)

PATRONAGE: What they are the patron saint of (if applicable)

Keep the total response concise (under 180 words) for an e-ink display.
No markdown formatting. If there are multiple saints, pick the most notable one.''')}"""

    # Replace date placeholder in custom prompts
    prompt = prompt.replace("{date}", date_str)
    prompt = prompt.replace("{month_day}", today.strftime("%m-%d"))

    api_provider = config.get("api_provider", "gemini").lower()
    temperature = config.get("temperature", 0.7)

    if api_provider == "openai":
        api_key = config.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        model = config.get("openai_model", "gpt-3.5-turbo")
        return call_openai_api(prompt, api_key, model, temperature)
    else:
        api_key = config.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
        return call_gemini_api(prompt, api_key, temperature)


def wrap_text(text, font, draw, max_width):
    """Wrap text to fit within max_width."""
    words = text.split()
    if not words:
        return ""

    lines = []
    current_line = words[0]

    for word in words[1:]:
        test_line = current_line + " " + word
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current_line = test_line
        else:
            lines.append(current_line)
            current_line = word
    lines.append(current_line)

    return "\n".join(lines)


def generate_display_image(text, config):
    """Generate an image with the saint info for e-ink display."""
    width = config.get("width", 800)
    height = config.get("height", 480)
    bg_color = config.get("background_color", "white")
    text_color = config.get("text_color", "black")
    max_font_size = config.get("max_font_size", 32)
    min_font_size = config.get("min_font_size", 12)
    margin = config.get("margin", 20)

    # Select font based on platform
    if platform.system() == "Darwin":
        font_path = "/Library/Fonts/Arial.ttf"
    else:
        font_path = "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"

    font_path = config.get("font_path", font_path)

    img = Image.new("RGB", (width, height), color=bg_color)
    draw = ImageDraw.Draw(img)

    # Find largest font size that fits
    font_size = max_font_size
    while font_size >= min_font_size:
        font = ImageFont.truetype(font_path, font_size)
        wrapped = wrap_text(text, font, draw, width - 2 * margin)
        bbox = draw.multiline_textbbox((0, 0), wrapped, font=font)
        text_height = bbox[3] - bbox[1]
        text_width = bbox[2] - bbox[0]

        if text_height <= height - 2 * margin and text_width <= width - 2 * margin:
            break
        font_size -= 2

    # Use minimum font size if nothing fits
    if font_size < min_font_size:
        font_size = min_font_size
        font = ImageFont.truetype(font_path, font_size)
        wrapped = wrap_text(text, font, draw, width - 2 * margin)
        bbox = draw.multiline_textbbox((0, 0), wrapped, font=font)
        text_height = bbox[3] - bbox[1]
        text_width = bbox[2] - bbox[0]

    # Center the text
    x = (width - text_width) // 2
    y = (height - text_height) // 2
    draw.multiline_text((x, y), wrapped, fill=text_color, font=font, align="left")

    return img


def main():
    """Main function to generate and display the saint of the day."""
    config_path = "saint_of_the_day_config.yml"

    if not os.path.exists(config_path):
        print(f"Config file not found: {config_path}")
        print("Please create the configuration file first.")
        return

    config = load_config(config_path)

    print("Fetching saint of the day...")
    today = datetime.now().strftime("%B %d, %Y")
    print(f"Date: {today}\n")

    saint_info = get_saint_of_the_day(config)
    print("=" * 50)
    print(saint_info)
    print("=" * 50 + "\n")

    # Generate display image
    img = generate_display_image(saint_info, config)
    output_path = config.get("output_path", "saint_of_the_day.bmp")
    img.save(output_path)
    print(f"Image saved to: {output_path}")

    # Display on e-ink if not on macOS
    if platform.system() != "Darwin":
        display_single_image(output_path)
        print("Displayed on e-ink screen.")
    else:
        print("Skipping e-ink display (running on macOS)")


if __name__ == "__main__":
    main()
