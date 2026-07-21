"""
Movie Slideshow module.

Displays image frames from a movie directory one at a time, cycling through
movies in a playlist. State is persisted in data/movies/_state.json.

Supported sources inside a movie directory:
  - Pre-extracted image files (.bmp, .png, .jpg, .jpeg)
  - Video files (.mp4, .mkv, .avi, .mov, .webm) — auto-extracted via ffmpeg
  - Animated GIFs (.gif) — auto-exploded into individual frames

Config keys under movie_slideshow:
  movies_dir:          data/movies
  active_movie:        ""    single movie folder name (overrides playlist)
  playlist:            []    list of movie folder names to cycle through
  output_path:         images/movie_display.bmp
  fill_mode:           fit   "fit" (letterbox) or "crop" (fill + center crop)
  frame_step:          1     frames to skip per display cycle
  extract_fps:         1     fps used when extracting frames from video files
  show_frame_counter:  true  draw "Movie  42/1200  [1/3]" chip in corner
"""

import os
import json
import subprocess
import platform
from PIL import Image, ImageDraw, ImageFont


SUPPORTED_EXTS = {".bmp", ".png", ".jpg", ".jpeg"}
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm"}
FRAMES_SUBDIR = "frames"


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def _state_path(movies_root):
    return os.path.join(movies_root, "_state.json")


def _load_state(movies_root):
    path = _state_path(movies_root)
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"movie_index": 0, "frame_index": 0}


def _save_state(movies_root, state):
    os.makedirs(movies_root, exist_ok=True)
    with open(_state_path(movies_root), "w") as f:
        json.dump(state, f)


# ---------------------------------------------------------------------------
# Frame extraction
# ---------------------------------------------------------------------------

def _extract_video(video_path, frames_dir, fps):
    """Run ffmpeg to extract frames from a video file into frames_dir."""
    name = os.path.splitext(os.path.basename(video_path))[0]
    pattern = os.path.join(frames_dir, f"{name}_%06d.jpg")
    print(f"[movie] Extracting {os.path.basename(video_path)} at {fps} fps — this may take a moment...")
    result = subprocess.run(
        ["ffmpeg", "-i", video_path, "-vf", f"fps={fps}", "-q:v", "2", pattern, "-y"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"[movie] ffmpeg error: {result.stderr[-300:]}")
    else:
        count = sum(1 for f in os.listdir(frames_dir) if f.startswith(name + "_") and f.endswith(".jpg"))
        print(f"[movie] Extracted {count} frames from {os.path.basename(video_path)}")


def _extract_gif(gif_path, frames_dir):
    """Explode an animated GIF into individual PNG frames in frames_dir."""
    name = os.path.splitext(os.path.basename(gif_path))[0]
    count = 0
    try:
        img = Image.open(gif_path)
        while True:
            img.copy().convert("RGB").save(os.path.join(frames_dir, f"{name}_{count:06d}.png"))
            count += 1
            img.seek(img.tell() + 1)
    except EOFError:
        pass
    except Exception as e:
        print(f"[movie] GIF extraction error for {os.path.basename(gif_path)}: {e}")
        return
    print(f"[movie] Extracted {count} frames from {os.path.basename(gif_path)}")


def _prepare_frames(movie_dir, extract_fps):
    """
    Ensure all video files and animated GIFs in movie_dir have been extracted
    to movie_dir/frames/. Uses mtime to skip already-extracted sources.

    Returns the directory to read frames from: frames/ when sources exist,
    movie_dir itself when only pre-extracted images are present.
    """
    video_files, gif_files = [], []
    try:
        for fname in sorted(os.listdir(movie_dir)):
            fpath = os.path.join(movie_dir, fname)
            ext = os.path.splitext(fname)[1].lower()
            if ext in VIDEO_EXTS:
                video_files.append(fpath)
            elif ext == ".gif":
                gif_files.append(fpath)
    except FileNotFoundError:
        return movie_dir

    if not video_files and not gif_files:
        return movie_dir  # only pre-extracted images — use the directory directly

    frames_dir = os.path.join(movie_dir, FRAMES_SUBDIR)
    os.makedirs(frames_dir, exist_ok=True)
    marker = os.path.join(frames_dir, ".extracted.json")

    try:
        with open(marker) as f:
            extracted = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        extracted = {}

    changed = False
    for src in sorted(video_files + gif_files):
        key = os.path.basename(src)
        mtime = str(os.path.getmtime(src))
        if extracted.get(key) == mtime:
            continue
        if os.path.splitext(key)[1].lower() in VIDEO_EXTS:
            _extract_video(src, frames_dir, fps=extract_fps)
        else:
            _extract_gif(src, frames_dir)
        extracted[key] = mtime
        changed = True

    if changed:
        with open(marker, "w") as f:
            json.dump(extracted, f)

    return frames_dir


def _list_frames(directory):
    """Return sorted list of image file paths in directory."""
    frames = []
    try:
        for fname in sorted(os.listdir(directory)):
            if os.path.splitext(fname)[1].lower() in SUPPORTED_EXTS:
                frames.append(os.path.join(directory, fname))
    except FileNotFoundError:
        pass
    return frames


# ---------------------------------------------------------------------------
# Image layout
# ---------------------------------------------------------------------------

def _fit_image(img, width, height):
    """Letterbox: fit entirely within frame, black bars on sides / top."""
    ratio = min(width / img.width, height / img.height)
    nw, nh = int(img.width * ratio), int(img.height * ratio)
    img = img.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGB", (width, height), "black")
    canvas.paste(img, ((width - nw) // 2, (height - nh) // 2))
    return canvas


def _crop_image(img, width, height):
    """Crop-fill: scale to fill frame, center-crop any overflow."""
    ratio = max(width / img.width, height / img.height)
    nw, nh = int(img.width * ratio), int(img.height * ratio)
    img = img.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - width) // 2, (nh - height) // 2
    return img.crop((left, top, left + width, top + height))


def _draw_frame_counter(canvas, movie_name, frame_idx, total, playlist_idx, playlist_len):
    """Draw a dark chip in the bottom-right corner with frame / playlist info."""
    draw = ImageDraw.Draw(canvas)
    w, h = canvas.size

    try:
        fp = (
            "/System/Library/Fonts/Supplemental/Arial.ttf"
            if platform.system() == "Darwin"
            else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
        )
        font = ImageFont.truetype(fp, 14)
    except Exception:
        font = ImageFont.load_default()

    parts = [f"{frame_idx + 1}/{total}"]
    if movie_name:
        parts.insert(0, movie_name)
    if playlist_len and playlist_len > 1:
        parts.append(f"[{playlist_idx + 1}/{playlist_len}]")
    label = "  ".join(parts)

    bb = draw.textbbox((0, 0), label, font=font)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    pad = 4
    rx = w - tw - pad * 2 - 4
    ry = h - th - pad * 2 - 4
    draw.rectangle([rx, ry, rx + tw + pad * 2, ry + th + pad * 2], fill=(0, 0, 0))
    draw.text((rx + pad, ry + pad), label, fill=(255, 255, 255), font=font)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def generate(config):
    """
    Load the next frame from the active movie (or playlist), resize to display
    dimensions, save as BMP, advance state. Returns output path.
    """
    cfg = config.get("movie_slideshow", {})
    movies_root = cfg.get("movies_dir", "data/movies")
    active_movie = cfg.get("active_movie", "")
    playlist = cfg.get("playlist", [])
    output_path = cfg.get("output_path", "movie_display.bmp")
    fill_mode = cfg.get("fill_mode", "fit")
    frame_step = max(1, int(cfg.get("frame_step", 1)))
    extract_fps = max(0.1, float(cfg.get("extract_fps", 1)))
    show_counter = cfg.get("show_frame_counter", True)
    width = config.get("width", 800)
    height = config.get("height", 480)

    movies = [active_movie] if active_movie else list(playlist)
    if not movies:
        return _placeholder(output_path, width, height,
                            "No movie selected.\n"
                            "Set active_movie or playlist in config under movie_slideshow.")

    state = _load_state(movies_root)
    movie_idx = state.get("movie_index", 0) % len(movies)
    frame_idx = state.get("frame_index", 0)

    movie_name = movies[movie_idx]
    movie_dir = os.path.join(movies_root, movie_name)
    frames_dir = _prepare_frames(movie_dir, extract_fps)
    frames = _list_frames(frames_dir)

    if not frames:
        return _placeholder(output_path, width, height,
                            f"No frames found for: {movie_name}\n\n"
                            "Drop .jpg/.png/.bmp image files, a video file,\n"
                            f"or an animated GIF into:\n{movie_dir}")

    frame_idx = frame_idx % len(frames)
    frame_path = frames[frame_idx]
    print(f"[movie] {movie_name}  frame {frame_idx + 1}/{len(frames)}: {os.path.basename(frame_path)}")

    img = Image.open(frame_path).convert("RGB")
    canvas = _crop_image(img, width, height) if fill_mode == "crop" else _fit_image(img, width, height)

    if show_counter:
        _draw_frame_counter(canvas, movie_name, frame_idx, len(frames),
                            playlist_idx=movie_idx,
                            playlist_len=len(movies))

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    canvas.save(output_path)

    # Advance; when this movie ends, move to the next in the playlist
    next_frame = frame_idx + frame_step
    if next_frame >= len(frames):
        next_movie = (movie_idx + 1) % len(movies)
        next_frame = 0
        print(f"[movie] Finished '{movie_name}', advancing to '{movies[next_movie]}'")
    else:
        next_movie = movie_idx

    _save_state(movies_root, {"movie_index": next_movie, "frame_index": next_frame})
    return output_path


def _placeholder(output_path, width, height, message):
    img = Image.new("RGB", (width, height), "black")
    draw = ImageDraw.Draw(img)
    try:
        fp = (
            "/Library/Fonts/Arial.ttf"
            if platform.system() == "Darwin"
            else "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"
        )
        font = ImageFont.truetype(fp, 26)
    except Exception:
        font = ImageFont.load_default()
    y = 40
    for line in message.split("\n"):
        draw.text((40, y), line, fill="white", font=font)
        y += 36
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path)
    return output_path


if __name__ == "__main__":
    import yaml
    with open("config.yml") as f:
        cfg = yaml.safe_load(f)
    path = generate(cfg)
    print(f"Output: {path}")
