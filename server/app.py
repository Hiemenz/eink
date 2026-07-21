"""
E-Ink Display Web Server

Run with:
    poetry run python server/app.py

Access at: http://localhost:5000  (or http://<pi-hostname>.local:5000)
"""

import os
import sys
import json
import subprocess
import zipfile
import io

import yaml
from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file, Response
from PIL import Image

# Make project root importable
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

CONFIG_PATH = os.path.join(ROOT, "config.yml")
MOVIES_ROOT = os.path.join(ROOT, "data", "movies")

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    cfg = load_config()
    movies = _list_movies()
    current_frame_info = _current_frame_info(cfg)
    return render_template(
        "index.html",
        config=cfg,
        movies=movies,
        current_frame_info=current_frame_info,
        module_options=["weather", "text", "saint_of_day", "wiki_image", "movie_slideshow",
                        "nasa_apod", "quote_of_day", "on_this_day", "moon_phase", "art_of_day"],
    )


@app.route("/module", methods=["POST"])
def set_module():
    module = request.form.get("module") or request.json.get("module", "weather")
    cfg = load_config()
    cfg["active_module"] = module
    save_config(cfg)
    return redirect(url_for("index"))


@app.route("/generate", methods=["POST"])
def generate_now():
    """Run main.py as a subprocess and stream output."""
    try:
        result = subprocess.run(
            ["poetry", "run", "python", "main.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stdout + result.stderr
        return jsonify({"ok": result.returncode == 0, "output": output})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "output": "Timed out after 120 seconds."})
    except Exception as e:
        return jsonify({"ok": False, "output": str(e)})


@app.route("/preview")
@app.route("/preview/<module_name>")
def preview(module_name=None):
    """Serve any module's output BMP as a PNG for the browser.

    /preview          — shows the currently active module
    /preview/<name>   — shows a specific module by name
    """
    cfg = load_config()
    active = module_name or cfg.get("active_module", "weather")

    bmp_path = _resolve_module_image(active, cfg)

    buf = io.BytesIO()
    try:
        if bmp_path and os.path.exists(bmp_path):
            img = Image.open(bmp_path).convert("RGB")
        else:
            img = Image.new("RGB", (800, 480), (180, 180, 180))
        img.save(buf, format="PNG")
    except Exception:
        Image.new("RGB", (800, 480), (180, 180, 180)).save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


@app.route("/preview-all")
def preview_all():
    """JSON list of all modules with their last-modified time and whether the image exists."""
    cfg = load_config()
    import time
    result = []
    from utils import MODULE_MAP
    for name in MODULE_MAP:
        path = _resolve_module_image(name, cfg)
        exists = bool(path and os.path.exists(path))
        mtime = os.path.getmtime(path) if exists else None
        result.append({
            "module": name,
            "path": path,
            "exists": exists,
            "age_seconds": round(time.time() - mtime) if mtime else None,
        })
    return jsonify(result)


@app.route("/config", methods=["GET"])
def get_config():
    return jsonify(load_config())


@app.route("/config", methods=["POST"])
def update_config():
    """Update config keys from JSON body. Nested keys supported via dot notation."""
    updates = request.json or {}
    cfg = load_config()
    for key, value in updates.items():
        parts = key.split(".")
        target = cfg
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value
    save_config(cfg)
    return jsonify({"ok": True})


@app.route("/interval", methods=["POST"])
def set_interval():
    seconds = int(request.form.get("seconds") or request.json.get("seconds", 300))
    cfg = load_config()
    cfg["update_interval"] = seconds
    save_config(cfg)
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Movie routes
# ---------------------------------------------------------------------------

@app.route("/movies")
def list_movies():
    return jsonify({"movies": _list_movies()})


@app.route("/movies/select", methods=["POST"])
def select_movie():
    movie = request.form.get("movie") or (request.json or {}).get("movie", "")
    cfg = load_config()
    cfg.setdefault("movie_slideshow", {})["active_movie"] = movie
    save_config(cfg)
    return redirect(url_for("index"))


@app.route("/movies/upload", methods=["POST"])
def upload_movie():
    """Upload a ZIP of image frames. Extracts to data/movies/<zip_name>/"""
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400

    file = request.files["file"]
    movie_name = os.path.splitext(file.filename)[0].replace(" ", "_")
    dest = os.path.join(MOVIES_ROOT, movie_name)
    os.makedirs(dest, exist_ok=True)

    try:
        with zipfile.ZipFile(file.stream) as z:
            for member in z.namelist():
                ext = os.path.splitext(member)[1].lower()
                if ext in {".bmp", ".png", ".jpg", ".jpeg"}:
                    fname = os.path.basename(member)
                    if fname:
                        with z.open(member) as src, open(os.path.join(dest, fname), "wb") as dst:
                            dst.write(src.read())
        return jsonify({"ok": True, "movie": movie_name, "dest": dest})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/movies/<movie_name>/preview")
def movie_preview(movie_name):
    """Serve the current frame of a given movie as PNG."""
    movie_dir = os.path.join(MOVIES_ROOT, movie_name)
    state_path = os.path.join(movie_dir, "state.json")
    frames = sorted([
        f for f in os.listdir(movie_dir)
        if os.path.splitext(f)[1].lower() in {".bmp", ".png", ".jpg", ".jpeg"}
    ]) if os.path.isdir(movie_dir) else []

    if not frames:
        img = Image.new("RGB", (800, 480), (30, 30, 30))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return send_file(buf, mimetype="image/png")

    idx = 0
    if os.path.exists(state_path):
        with open(state_path) as f:
            idx = json.load(f).get("frame_index", 0) % len(frames)

    frame_path = os.path.join(movie_dir, frames[idx])
    img = Image.open(frame_path).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_module_image(module_name: str, cfg: dict) -> str:
    """Return the absolute path to a module's most recent output BMP, or '' if unknown."""
    # Weather is special: path includes the station name
    if module_name == "weather":
        station = cfg.get("station", {}).get("name", "KOHX")
        return os.path.join(ROOT, "radar", f"eink_quantized_display_{station}.bmp")

    # All other modules declare their output_path in their config section
    section = cfg.get(module_name, {})
    rel = section.get("output_path", "")
    if rel:
        return os.path.join(ROOT, rel)

    # Fallback: images/<module_name>.bmp convention
    return os.path.join(ROOT, "images", f"{module_name.replace('_', '_')}.bmp")


def _list_movies():
    if not os.path.isdir(MOVIES_ROOT):
        return []
    return [
        d for d in sorted(os.listdir(MOVIES_ROOT))
        if os.path.isdir(os.path.join(MOVIES_ROOT, d))
    ]


def _current_frame_info(cfg):
    movie = cfg.get("movie_slideshow", {}).get("active_movie", "")
    if not movie:
        return None
    movie_dir = os.path.join(MOVIES_ROOT, movie)
    frames = sorted([
        f for f in os.listdir(movie_dir)
        if os.path.splitext(f)[1].lower() in {".bmp", ".png", ".jpg", ".jpeg"}
    ]) if os.path.isdir(movie_dir) else []
    state_path = os.path.join(movie_dir, "state.json")
    idx = 0
    if os.path.exists(state_path):
        with open(state_path) as f:
            idx = json.load(f).get("frame_index", 0)
    return {"movie": movie, "frame": idx, "total": len(frames)}


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"Starting server — open http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
