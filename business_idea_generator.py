#!/usr/bin/env python3
"""
Business Idea Generator for E-Ink Display

Generates unique money-making business ideas with mini business plans
and displays them on an e-ink display. Each run produces a different idea.
"""

import os
import json
import uuid
import hashlib
import platform
import requests
import yaml
from PIL import Image, ImageDraw, ImageFont

if platform.system() != "Darwin":
    from display import display_single_image


def load_config(yaml_path="business_idea_config.yml"):
    """Load configuration from YAML file."""
    with open(yaml_path, "r") as f:
        return yaml.safe_load(f)


def generate_unique_seed():
    """Generate a unique seed to ensure different ideas each time."""
    return str(uuid.uuid4())


def call_gemini_api(prompt, api_key, temperature=1.5):
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


def call_openai_api(prompt, api_key, model="gpt-3.5-turbo", temperature=1.2):
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


def generate_business_idea(config):
    """Generate a unique business idea using the configured API."""
    seed = generate_unique_seed()

    prompt = f"""Seed: {seed}

{config.get('prompt', '''Generate a unique, creative money-making business idea. Include:

1. IDEA: A catchy one-line business name/concept
2. WHAT: What the business does (1-2 sentences)
3. WHY: Why it will make money (1 sentence)
4. START: How to start with minimal investment (1 sentence)

Keep it concise for an e-ink display. Be creative and practical.
Format it cleanly without markdown symbols.''')}"""

    api_provider = config.get("api_provider", "gemini").lower()
    temperature = config.get("temperature", 1.5)

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
    """Generate an image with the business idea for e-ink display."""
    width = config.get("width", 800)
    height = config.get("height", 480)
    bg_color = config.get("background_color", "white")
    text_color = config.get("text_color", "black")
    max_font_size = config.get("max_font_size", 40)
    min_font_size = config.get("min_font_size", 14)
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


def save_idea_history(idea, history_file="business_ideas_history.json"):
    """Save generated idea to history to track uniqueness."""
    history = []
    if os.path.exists(history_file):
        with open(history_file, "r") as f:
            history = json.load(f)

    history.append({
        "id": generate_unique_seed(),
        "idea": idea
    })

    # Keep only last 100 ideas
    history = history[-100:]

    with open(history_file, "w") as f:
        json.dump(history, f, indent=2)


def main():
    """Main function to generate and display a business idea."""
    config_path = "business_idea_config.yml"

    if not os.path.exists(config_path):
        print(f"Config file not found: {config_path}")
        print("Please create the configuration file first.")
        return

    config = load_config(config_path)

    print("Generating unique business idea...")
    idea = generate_business_idea(config)
    print("\n" + "=" * 50)
    print(idea)
    print("=" * 50 + "\n")

    # Save to history
    history_file = config.get("history_file", "business_ideas_history.json")
    save_idea_history(idea, history_file)

    # Generate display image
    img = generate_display_image(idea, config)
    output_path = config.get("output_path", "business_idea.bmp")
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
