"""
Movie Slideshow module.

Displays image frames from a directory one at a time.
State (current frame index) is persisted in data/movies/<movie>/state.json
so each call to generate() advances to the next frame.

Drop pre-extracted frames (any name, any image format) into:
    data/movies/<movie_name>/

Frames are displayed in alphabetical order.
"""

import os
import json
from PIL import Image


SUPPORTED_EXTS = {".bmp", ".png", ".jpg", ".jpeg", ".gif"}


def _state_path(movie_dir):
    return os.path.join(movie_dir, "state.json")


def _load_state(movie_dir):
    path = _state_path(movie_dir)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"frame_index": 0}


def _save_state(movie_dir, state):
    with open(_state_path(movie_dir), 'w') as f:
        json.dump(state, f)


def _list_frames(movie_dir):
    """Return sorted list of image file paths in the directory."""
    frames = []
    try:
        for fname in sorted(os.listdir(movie_dir)):
            ext = os.path.splitext(fname)[1].lower()
            if ext in SUPPORTED_EXTS:
                frames.append(os.path.join(movie_dir, fname))
    except FileNotFoundError:
        pass
    return frames


def _fit_image(img, width, height):
    """Letterbox: fit entirely within frame, black bars on sides/top."""
    ratio = min(width / img.width, height / img.height)
    new_w = int(img.width * ratio)
    new_h = int(img.height * ratio)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    canvas = Image.new("RGB", (width, height), "black")
    canvas.paste(img, ((width - new_w) // 2, (height - new_h) // 2))
    return canvas


def _crop_image(img, width, height):
    """Crop-fill: scale to fill frame, center-crop any overflow."""
    ratio = max(width / img.width, height / img.height)
    new_w = int(img.width * ratio)
    new_h = int(img.height * ratio)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - width) // 2
    top = (new_h - height) // 2
    img = img.crop((left, top, left + width, top + height))
    canvas = Image.new("RGB", (width, height), "black")
    canvas.paste(img, (0, 0))
    return canvas


def generate(config):
    """
    Load the next frame from the active movie directory,
    resize to 800x480, save as BMP, advance frame index.
    Returns output path.
    """
    slideshow_cfg = config.get("movie_slideshow", {})
    movies_root = slideshow_cfg.get("movies_dir", "data/movies")
    active_movie = slideshow_cfg.get("active_movie", "")
    output_path = slideshow_cfg.get("output_path", "movie_display.bmp")
    fill_mode = slideshow_cfg.get("fill_mode", "fit")   # "fit" or "crop"
    width = config.get("width", 800)
    height = config.get("height", 480)

    if not active_movie:
        return _placeholder(output_path, width, height, "No movie selected.\nAdd frames to data/movies/<name>/ and set active_movie in config.")

    movie_dir = os.path.join(movies_root, active_movie)
    frames = _list_frames(movie_dir)

    if not frames:
        return _placeholder(output_path, width, height, f"No frames found in:\n{movie_dir}\n\nDrop .jpg/.png/.bmp files there.")

    state = _load_state(movie_dir)
    idx = state.get("frame_index", 0) % len(frames)
    frame_path = frames[idx]

    print(f"[movie] Displaying frame {idx + 1}/{len(frames)}: {os.path.basename(frame_path)}")

    img = Image.open(frame_path).convert("RGB")

    if fill_mode == "crop":
        canvas = _crop_image(img, width, height)
    else:
        canvas = _fit_image(img, width, height)

    canvas.save(output_path)

    # Advance frame index (wraps to 0)
    next_idx = (idx + 1) % len(frames)
    _save_state(movie_dir, {"frame_index": next_idx})
    print(f"[movie] Next frame index: {next_idx}")

    return output_path


def _placeholder(output_path, width, height, message):
    import platform
    from PIL import ImageDraw, ImageFont
    img = Image.new("RGB", (width, height), "black")
    draw = ImageDraw.Draw(img)
    try:
        fp = "/Library/Fonts/Arial.ttf" if platform.system() == "Darwin" else "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"
        font = ImageFont.truetype(fp, 26)
    except Exception:
        font = ImageFont.load_default()
    y = 40
    for line in message.split("\n"):
        draw.text((40, y), line, fill="white", font=font)
        y += 36
    img.save(output_path)
    return output_path


if __name__ == "__main__":
    import yaml
    with open("config.yml") as f:
        cfg = yaml.safe_load(f)
    path = generate(cfg)
    print(f"Output: {path}")
