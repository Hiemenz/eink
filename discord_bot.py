"""
E-Ink Discord Bot

Controls the e-ink display via Discord commands. Add your bot token and
channel ID to the 'discord:' section of config.yml, then run:

    poetry run python discord_bot.py

Commands (default prefix: !)
    !display <module>      Switch active module and refresh the display
    !set <key> <value>     Update a config value (dot notation supported)
    !refresh               Force a display refresh with the current module
    !status                Show current display state
    !modules               List all available modules and their configurable args
    !help_display          Show command reference
"""

import asyncio
import io
import json
import os
import sys
from typing import Any, Optional

import discord
import yaml
from discord.ext import commands, tasks
from dotenv import load_dotenv
from PIL import Image

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "config.yml")
BOT_STATE_PATH = os.path.join(ROOT, "bot_state.json")

# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------

ALL_MODULES = [
    "weather",
    "text",
    "saint_of_day",
    "wiki_image",
    "movie_slideshow",
    "nasa_apod",
    "quote_of_day",
    "on_this_day",
    "moon_phase",
    "art_of_day",
    "chess_puzzle",
    "sudoku_puzzle",
    "poem_of_day",
    "news_headlines",
    "flight_radar",
    "franklin_cam",
    "parking_garage",
    "module_cycler",
    "brain_status",
    "interesting_fact",
    "qrcode_display",
    "claude_news",
    "questions",
    "terminal",
    "crypto_market",
    "game_of_life",
]

# Pre-flight config checks per module
MODULE_CONFIG_CHECKS: dict = {
    "movie_slideshow": {
        "required": {
            "movie_slideshow.active_movie": "Name of the movie folder inside data/movies/ (e.g. `interstellar`)"
        }
    },
    "flight_radar": {
        "recommended": {
            "flight_radar.opensky_username": "OpenSky Network username — increases API rate limits",
            "flight_radar.opensky_password": "OpenSky Network password",
        }
    },
    "nasa_apod": {
        "recommended": {
            "nasa_apod.api_key": "NASA API key from api.nasa.gov — DEMO_KEY works but is rate-limited"
        }
    },
}


def _check_module_config(module: str, cfg: dict) -> tuple[list, list]:
    """Return (missing_required, missing_recommended) as lists of (key, description) tuples."""
    checks = MODULE_CONFIG_CHECKS.get(module, {})
    missing_required = []
    missing_recommended = []

    def _get_nested(d: dict, dotkey: str):
        parts = dotkey.split(".")
        val = d
        for part in parts:
            if not isinstance(val, dict):
                return None
            val = val.get(part)
        return val

    for key, desc in checks.get("required", {}).items():
        val = _get_nested(cfg, key)
        if val is None or val == "":
            missing_required.append((key, desc))

    for key, desc in checks.get("recommended", {}).items():
        val = _get_nested(cfg, key)
        if val is None or val == "":
            missing_recommended.append((key, desc))

    return missing_required, missing_recommended


# When !set <module_name> <value> is used, map to this primary config key
MODULE_PRIMARY_ARG: dict[str, str] = {
    "text":            "text.message",
    "sudoku_puzzle":   "sudoku_puzzle.num_clues",
    "flight_radar":    "flight_radar.radius_deg",
    "movie_slideshow": "movie_slideshow.active_movie",
    "nasa_apod":       "nasa_apod.api_key",
    "questions":       "questions.interval_minutes",
    "qrcode_display":  "qrcode_display.text",
}

# Per-module configurable args shown in !modules / !set hints
MODULE_ARGS: dict = {
    "text": {
        "text.message": "Message to display (set via !text <message>)",
    },
    "weather": {
        "station":               "Radar station code (e.g. KOHX, KFWS, KTLX)",
        "radar_mode":            "Display mode: crop | fit | panel",
        "panel_header":          "Header text shown in panel (e.g. 'Franklin, TN')",
        "panel_width":           "Width of conditions panel in pixels (panel mode only)",
        "interesting_threshold": "% colored pixels below which station is 'boring' (0–100)",
    },
    "flight_radar": {
        "flight_radar.radius_deg":       "Search radius in degrees around forecast_location",
        "flight_radar.map_zoom":         "Map tile zoom level (1–18, default 9)",
        "flight_radar.opensky_username": "OpenSky Network username (for higher rate limits)",
        "flight_radar.opensky_password": "OpenSky Network password",
    },
    "movie_slideshow": {
        "movie_slideshow.active_movie": "Folder name inside data/movies/ to display",
    },
    "nasa_apod": {
        "nasa_apod.api_key": "NASA API key (default DEMO_KEY — free at api.nasa.gov)",
    },
    "franklin_cam": {
        "franklin_cam.label": "Label shown on the camera display",
    },
    "module_cycler": {
        "module_cycler.modules":          "Comma-separated module list (e.g. weather,nasa_apod,moon_phase)",
        "module_cycler.interval_minutes": "Minutes to stay on each module before rotating (default 60)",
    },
    "claude_news": {
        "claude_news.output_path": "Output BMP path (default images/claude_news.bmp)",
    },
    "interesting_fact": {
        "interesting_fact.interval_minutes": "Minutes between fact rotations (default 60)",
        "interesting_fact.csv_file": "Path to CSV with topic,question columns",
    },
    "qrcode_display": {
        "qrcode_display.text":          "Text or URL to encode as QR code",
        "qrcode_display.label":         "Label shown below the QR code",
        "qrcode_display.sublabel":      "Smaller secondary label (optional)",
        "qrcode_display.wifi_ssid":     "WiFi network name (auto-formats as WiFi QR)",
        "qrcode_display.wifi_password": "WiFi password",
        "qrcode_display.wifi_security": "WPA | WEP | nopass (default WPA)",
    },
    "questions": {
        "questions.interval_minutes": "Minutes between question changes (default 15)",
        "questions.csv_file":         "Path to CSV with topic,question columns",
        "questions.force_new":        "Set true to immediately pick a new random question",
    },
    "game_of_life": {
        "game_of_life.cell_size":       "Pixels per cell (default 10 → 80x48 grid)",
        "game_of_life.initial_density": "Fraction of cells alive on new game (0.0–1.0)",
        "game_of_life.start_new":       "Set true to discard saved state and restart",
        "game_of_life.wrap":            "true/false — toroidal (wrap-around) edges",
        "game_of_life.show_generation": "true/false — show generation counter overlay",
        "game_of_life.show_population": "true/false — show live cell count overlay",
    },
    "terminal": {
        "terminal.output_path": "Output BMP path (default images/terminal_display.bmp)",
    },
    "forecast_location": {
        "forecast_location.latitude":  "Latitude for weather/flight forecast",
        "forecast_location.longitude": "Longitude for weather/flight forecast",
        "forecast_location.name":      "Location display name",
    },
    "sudoku_puzzle": {
        "sudoku_puzzle.num_clues": "Number of given clues (default 35; fewer = harder)",
    },
}

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _deep_merge(base: dict, overrides: dict, safe: bool = False) -> dict:
    """Recursively merge overrides into base without modifying either.

    When safe=True, a scalar override is dropped if the base value is a dict,
    preventing stale bot_state entries from corrupting module config dicts.
    """
    result = dict(base)
    for k, v in overrides.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v, safe=safe)
        elif safe and k in result and isinstance(result[k], dict) and not isinstance(v, dict):
            pass  # drop incompatible scalar override
        else:
            result[k] = v
    return result


def load_bot_state() -> dict:
    if not os.path.exists(BOT_STATE_PATH):
        return {}
    with open(BOT_STATE_PATH) as f:
        return json.load(f)


def update_bot_state(key: str, value: Any) -> None:
    """Write a single dot-notation key into bot_state.json."""
    state = load_bot_state()
    set_nested(state, key, value)
    with open(BOT_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)
    return _deep_merge(cfg, load_bot_state(), safe=True)


def find_station(code: str, cfg: dict) -> Optional[dict]:
    """Return the full station dict from the stations list matching the given code."""
    for s in cfg.get("stations", []):
        if isinstance(s, dict) and s.get("name", "").upper() == code.upper():
            return s
    return None


def cast_value(value: str):
    """Try to cast a string to int, float, bool, or leave as string."""
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def set_nested(cfg: dict, key: str, value) -> None:
    """Write a value into cfg using dot-notation key."""
    parts = key.split(".")
    target = cfg
    for part in parts[:-1]:
        if not isinstance(target.get(part), dict):
            target[part] = {}
        target = target[part]
    target[parts[-1]] = value


# ---------------------------------------------------------------------------
# Subprocess helper
# ---------------------------------------------------------------------------


async def run_main() -> tuple[bool, str]:
    """Run main.py in the project root. Returns (success, output_tail)."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, os.path.join(ROOT, "main.py"),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=ROOT,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
    except asyncio.TimeoutError:
        proc.kill()
        return False, "Timed out after 120 seconds."

    output = stdout.decode(errors="replace").strip()
    tail = "\n".join(output.splitlines()[-15:]) if output else "(no output)"
    return proc.returncode == 0, tail


# ---------------------------------------------------------------------------
# Display image helper
# ---------------------------------------------------------------------------


def get_output_image_path(cfg: dict) -> Optional[str]:
    """Return the BMP output path for the current active module."""
    active = cfg.get("active_module", "weather")
    station_name = cfg.get("station", {}).get("name", "KOHX") if isinstance(cfg.get("station"), dict) else "KOHX"

    def _p(key: str, default: str) -> str:
        val = cfg.get(key)
        sub = val.get("output_path", default) if isinstance(val, dict) else default
        return os.path.join(ROOT, sub)

    path_map = {
        "weather":         os.path.join(ROOT, "radar", f"eink_quantized_display_{station_name}.bmp"),
        "text":            _p("text",            "images/text_display.bmp"),
        "saint_of_day":    _p("saint_of_day",    "images/saint_display.bmp"),
        "wiki_image":      _p("wiki_image",       "images/wiki_display.bmp"),
        "movie_slideshow": _p("movie_slideshow",  "images/movie_display.bmp"),
        "nasa_apod":       _p("nasa_apod",        "images/nasa_apod.bmp"),
        "quote_of_day":    _p("quote_of_day",     "images/quote_display.bmp"),
        "on_this_day":     _p("on_this_day",      "images/onthisday_display.bmp"),
        "moon_phase":      _p("moon_phase",       "images/moon_display.bmp"),
        "art_of_day":      _p("art_of_day",       "images/art_display.bmp"),
        "chess_puzzle":    _p("chess_puzzle",     "images/chess_display.bmp"),
        "sudoku_puzzle":   _p("sudoku_puzzle",    "images/sudoku_display.bmp"),
        "poem_of_day":     _p("poem_of_day",      "images/poem_display.bmp"),
        "news_headlines":  _p("news_headlines",   "images/news_display.bmp"),
        "flight_radar":    _p("flight_radar",     "images/flight_display.bmp"),
        "franklin_cam":    _p("franklin_cam",     "images/franklin_cam.bmp"),
        "parking_garage":  _p("parking_garage",   "images/parking_display.bmp"),
        "claude_news":     _p("claude_news",       "images/claude_news.bmp"),
        "interesting_fact": _p("interesting_fact", "images/interesting_fact.bmp"),
        "qrcode_display":  _p("qrcode_display",   "images/qrcode_display.bmp"),
        "questions":       _p("questions",         "images/questions_display.bmp"),
        "terminal":        _p("terminal",          "images/terminal_display.bmp"),
        "crypto_market":   _p("crypto_market",     "images/crypto_market.bmp"),
    }

    # module_cycler delegates to whatever module it last ran
    if active == "module_cycler":
        import json
        state_path = os.path.join(ROOT, cfg.get("module_cycler", {}).get("state_file", "data/cycler_state.json"))
        last = None
        if os.path.exists(state_path):
            with open(state_path) as f:
                last = json.load(f).get("last_module")
        if last and last in path_map:
            return path_map[last]
        return None

    return path_map.get(active)


async def send_display_image(channel: discord.abc.Messageable, cfg: dict) -> None:
    """Post the current display image to the given channel as a PNG."""
    path = get_output_image_path(cfg)
    if not path or not os.path.exists(path):
        return

    try:
        img = Image.open(path).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        await channel.send(file=discord.File(buf, filename="display.png"))
    except Exception as e:
        await channel.send(f"(Could not attach display image: {e})")


# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------


_STATIC_MODULES = {"text", "qrcode_display", "terminal"}


def _next_update_str(module: str, cfg: dict, module_intervals: dict, global_fallback: int) -> str:
    """Return a human-readable 'next update in X min' string for the given module."""
    if module in _STATIC_MODULES:
        return "static — no auto-refresh"
    module_cfg = cfg.get(module, {})
    if isinstance(module_cfg, dict) and "update_interval" in module_cfg:
        interval = int(module_cfg["update_interval"])
    else:
        interval = module_intervals.get(module, global_fallback)
    mins = interval // 60
    if mins >= 1440:
        return f"next update in ~{mins // 1440}d"
    if mins >= 60:
        return f"next update in ~{mins // 60}h"
    return f"next update in ~{mins}min"


def make_bot(prefix: str) -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True
    return commands.Bot(command_prefix=prefix, intents=intents, help_command=None)


bot: commands.Bot = None  # assigned in main()
ALLOWED_CHANNEL: int = 0  # assigned in main()


def channel_guard():
    """Command check: only respond in the configured channel (or DM if channel_id=0)."""
    async def predicate(ctx: commands.Context) -> bool:
        if ALLOWED_CHANNEL == 0:
            return True
        return ctx.channel.id == ALLOWED_CHANNEL
    return commands.check(predicate)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@channel_guard()
async def cmd_display(ctx: commands.Context, module: str = None):
    """Switch the active module and refresh the display."""
    if module is None:
        numbered = "\n".join(f"{i+1}. {m}" for i, m in enumerate(ALL_MODULES))
        prompt = await ctx.send(
            f"**Which module?** Reply with a number:\n```\n{numbered}\n```"
        )

        def _check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        try:
            reply = await bot.wait_for("message", check=_check, timeout=60)
            choice = reply.content.strip()
            if choice.isdigit() and 1 <= int(choice) <= len(ALL_MODULES):
                module = ALL_MODULES[int(choice) - 1]
            elif choice in ALL_MODULES:
                module = choice
            else:
                await ctx.send(f"Invalid choice `{choice}`. Type a number 1–{len(ALL_MODULES)}.")
                return
        except asyncio.TimeoutError:
            await prompt.edit(content="Timed out — no module selected.")
            return

    if module not in ALL_MODULES:
        embed = discord.Embed(
            title="Unknown module",
            description=f"`{module}` is not a valid module.\nRun `!modules` to see available options.",
            color=discord.Color.red(),
        )
        await ctx.send(embed=embed)
        return

    cfg = load_config()
    missing_required, missing_recommended = _check_module_config(module, cfg)

    if missing_required:
        lines = [f"`!set {k} <value>` — {desc}" for k, desc in missing_required]
        embed = discord.Embed(
            title=f"Cannot display `{module}` — required config missing",
            description="\n".join(lines),
            color=discord.Color.red(),
        )
        await ctx.send(embed=embed)
        return

    if missing_recommended:
        lines = [f"`!set {k} <value>` — {desc}" for k, desc in missing_recommended]
        warn_embed = discord.Embed(
            title=f"Optional config for `{module}`",
            description="These settings are recommended but not required:\n" + "\n".join(lines),
            color=discord.Color.yellow(),
        )
        await ctx.send(embed=warn_embed)

    update_bot_state("active_module", module)

    msg = await ctx.send(f"Switching display to **{module}**...")

    success, output = await run_main()

    if success:
        next_upd = _next_update_str(module, load_config(), MODULE_INTERVALS, 21600)
        embed = discord.Embed(
            title=f"Display updated — {module}",
            description=next_upd,
            color=discord.Color.green(),
        )
        embed.add_field(name="Output", value=f"```{output[:900]}```", inline=False)
    else:
        embed = discord.Embed(
            title="Display update failed",
            description=f"Module: `{module}`",
            color=discord.Color.red(),
        )
        embed.add_field(name="Error", value=f"```{output[:900]}```", inline=False)

    await msg.edit(content=None, embed=embed)

    if success:
        await send_display_image(ctx.channel, load_config())
        args = MODULE_ARGS.get(module)
        if args:
            opts = discord.Embed(
                title=f"{module} — configurable options",
                description="\n".join(f"`!set {k}` — {v}" for k, v in args.items()),
                color=discord.Color.og_blurple(),
            )
            await ctx.send(embed=opts)


@channel_guard()
async def cmd_text(ctx: commands.Context, *, message: str = None):
    """Display a custom text message on the e-ink screen."""
    if not message:
        await ctx.send("Usage: `!text <your message here>`")
        return

    update_bot_state("text.message", message)
    update_bot_state("active_module", "text")

    msg = await ctx.send(f"Displaying: **{message[:80]}{'…' if len(message) > 80 else ''}**")

    success, output = await run_main()

    if success:
        embed = discord.Embed(title="Display updated — text", color=discord.Color.green())
        embed.add_field(name="Message", value=message[:900], inline=False)
    else:
        embed = discord.Embed(title="Display update failed", color=discord.Color.red())
        embed.add_field(name="Error", value=f"```{output[:900]}```", inline=False)

    await msg.edit(content=None, embed=embed)

    if success:
        await send_display_image(ctx.channel, load_config())


@channel_guard()
async def cmd_questions(ctx: commands.Context, minutes: str = None):
    """Switch to the questions module, asking for the interval if not provided."""
    interval = None

    if minutes is not None:
        try:
            interval = int(minutes)
        except ValueError:
            await ctx.send(f"`{minutes}` isn't a valid number. How many minutes between questions?")

    if interval is None:
        prompt = await ctx.send("How many minutes between questions? (e.g. `15`)")

        def _check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        try:
            reply = await bot.wait_for("message", check=_check, timeout=60)
            interval = int(reply.content.strip())
        except asyncio.TimeoutError:
            await prompt.edit(content="Timed out — using 15 minutes.")
            interval = 15
        except ValueError:
            await ctx.send(f"`{reply.content.strip()}` isn't a valid number — using 15 minutes.")
            interval = 15

    update_bot_state("questions.interval_minutes", interval)
    update_bot_state("active_module", "questions")

    msg = await ctx.send(f"Switching to **questions** — rotating every **{interval} min**...")
    success, output = await run_main()

    if success:
        embed = discord.Embed(title="Display updated — questions", color=discord.Color.green())
        embed.add_field(name="Interval", value=f"{interval} minutes", inline=True)
        embed.add_field(name="Output", value=f"```{output[:800]}```", inline=False)
    else:
        embed = discord.Embed(title="Display update failed", color=discord.Color.red())
        embed.add_field(name="Error", value=f"```{output[:900]}```", inline=False)

    await msg.edit(content=None, embed=embed)

    if success:
        await send_display_image(ctx.channel, load_config())



# ---------------------------------------------------------------------------
# Video download command
# ---------------------------------------------------------------------------

@channel_guard()
async def cmd_video(ctx: commands.Context, url: str = None):
    """Download a YouTube (or any yt-dlp-supported) video and extract frames for the movie slideshow.

    Usage: !video <url>
    The bot prompts for: movie name, frame interval, start offset, and fill mode.
    The video is deleted after frame extraction; frames are kept in data/movies/<name>/.
    """
    import shutil
    import subprocess
    import tempfile
    import re

    def _ask(prompt_text, default=None, validate=None):
        """Return a coroutine that sends a prompt and waits for a reply."""
        async def _inner():
            hint = f" (default: {default})" if default is not None else ""
            msg = await ctx.send(f"{prompt_text}{hint}")

            def _check(m):
                return m.author == ctx.author and m.channel == ctx.channel

            try:
                reply = await bot.wait_for("message", check=_check, timeout=90)
                val = reply.content.strip()
                if not val and default is not None:
                    return default
                if validate:
                    return validate(val)
                return val
            except asyncio.TimeoutError:
                await msg.edit(content=f"{prompt_text} — timed out, using default: {default}")
                return default
        return _inner()

    if url is None:
        url = await _ask("What is the YouTube (or video) URL?")
        if not url:
            await ctx.send("No URL provided — cancelled.")
            return

    # Sanitise URL
    url = url.strip("<>")

    # Ask for movie name
    def _slug(v):
        return re.sub(r"[^\w\-]", "_", v.strip())[:40] or "movie"

    movie_name = await _ask(
        "Movie/folder name? (letters, numbers, underscores)",
        default="movie",
        validate=_slug,
    )

    # Frame interval in video-minutes
    def _pos_float(v):
        try:
            f = float(v)
            return max(0.1, f)
        except ValueError:
            return 1.0

    interval_min = await _ask(
        "Every how many **minutes** of video to grab one frame?",
        default=1.0,
        validate=_pos_float,
    )

    # Start offset in seconds
    def _nonneg_int(v):
        try:
            return max(0, int(v))
        except ValueError:
            return 0

    start_sec = await _ask(
        "Start at what second? (0 = beginning)",
        default=0,
        validate=_nonneg_int,
    )

    # Fill mode
    def _fill_mode(v):
        v = v.strip().lower()
        return "crop" if v in ("crop", "fill", "c") else "fit"

    fill_mode = await _ask(
        "Frame scaling: `crop` (fill frame, may clip edges) or `fit` (letterbox, black bars)?",
        default="crop",
        validate=_fill_mode,
    )

    movies_root = os.path.join(ROOT, "data", "movies")
    movie_dir = os.path.join(movies_root, movie_name)
    os.makedirs(movie_dir, exist_ok=True)

    status_msg = await ctx.send(f"⬇️ Downloading video to a temp directory...")

    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = os.path.join(tmpdir, "video.%(ext)s")
        ydl_opts = {
            "outtmpl": video_path,
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "quiet": True,
            "no_warnings": True,
        }

        try:
            import yt_dlp
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                title = info.get("title", movie_name)
                duration = info.get("duration", 0)
        except Exception as e:
            await status_msg.edit(content=f"❌ Download failed: {e}")
            return

        # Find the downloaded file
        video_file = None
        for fname in os.listdir(tmpdir):
            if not fname.endswith(".json"):
                video_file = os.path.join(tmpdir, fname)
                break

        if not video_file or not os.path.exists(video_file):
            await status_msg.edit(content="❌ Could not find downloaded video file.")
            return

        await status_msg.edit(content=f"🎞️ **{title}** ({duration}s) — extracting frames every {interval_min}min from {start_sec}s…")

        # Clear existing frames in the movie_dir
        for fname in os.listdir(movie_dir):
            if os.path.splitext(fname)[1].lower() in {".jpg", ".jpeg", ".png", ".bmp"}:
                os.remove(os.path.join(movie_dir, fname))

        # ffmpeg: extract one frame every N seconds, starting at start_sec
        interval_sec = interval_min * 60.0
        frame_pattern = os.path.join(movie_dir, "frame_%05d.jpg")

        # Compute scale/crop filter for 800x480
        if fill_mode == "crop":
            vf = r"scale=iw*max(800/iw\,480/ih):ih*max(800/iw\,480/ih),crop=800:480"
        else:
            vf = "scale=800:480:force_original_aspect_ratio=decrease,pad=800:480:(ow-iw)/2:(oh-ih)/2:black"

        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_sec),
            "-i", video_file,
            "-vf", f"fps=1/{interval_sec:.2f},{vf}",
            "-q:v", "3",
            frame_pattern,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *ffmpeg_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            if proc.returncode != 0:
                err = stderr.decode(errors="replace")[-400:]
                await status_msg.edit(content=f"❌ ffmpeg failed:\n```{err}```")
                return
        except asyncio.TimeoutError:
            await status_msg.edit(content="❌ ffmpeg timed out (>5 min).")
            return
        except FileNotFoundError:
            await status_msg.edit(content="❌ `ffmpeg` not found — install it on the Pi with `sudo apt install ffmpeg`.")
            return

    # Video temp dir is cleaned up automatically; frames are in movie_dir
    frame_count = len([f for f in os.listdir(movie_dir) if f.endswith(".jpg")])
    if frame_count == 0:
        await status_msg.edit(content="❌ No frames extracted — check the URL and interval.")
        return

    # Update bot state
    update_bot_state("movie_slideshow.active_movie", movie_name)
    update_bot_state("movie_slideshow.fill_mode", fill_mode)
    update_bot_state("active_module", "movie_slideshow")

    # Reset frame index
    from modules.movie_slideshow import _save_state
    _save_state(movie_dir, {"frame_index": 0})

    await status_msg.edit(
        content=(
            f"✅ **{title}**\n"
            f"`{frame_count}` frames extracted into `data/movies/{movie_name}/`\n"
            f"Interval: {interval_min}min | Start: {start_sec}s | Mode: {fill_mode}\n"
            f"Switching display to **movie_slideshow**…"
        )
    )

    success, output = await run_main()
    if success:
        await send_display_image(ctx.channel, load_config())
        embed = discord.Embed(
            title=f"🎬 Movie slideshow ready — {movie_name}",
            description=(
                f"{frame_count} frames • every {interval_min}min • {fill_mode} mode\n"
                f"Use `!movie skip [N]` to jump frames, `!movie back [N]` to go back."
            ),
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)
    else:
        await ctx.send(embed=discord.Embed(
            title="Display switch failed",
            description=f"```{output[:900]}```",
            color=discord.Color.red(),
        ))


@channel_guard()
async def cmd_movie(ctx: commands.Context, action: str = None, amount: str = "1"):
    """Control the movie slideshow — skip, back, goto, or reset.

    !movie skip [N]   — advance N frames forward (default 1)
    !movie back [N]   — go back N frames (default 1)
    !movie goto <N>   — jump to frame N (1-based)
    !movie reset      — restart from frame 1
    !movie status     — show current frame info
    """
    cfg = load_config()
    movies_root = os.path.join(ROOT, cfg.get("movie_slideshow", {}).get("movies_dir", "data/movies"))
    active_movie = cfg.get("movie_slideshow", {}).get("active_movie", "")

    if not active_movie:
        await ctx.send("No active movie. Use `!video <url>` to set one up.")
        return

    from modules.movie_slideshow import _load_state, _save_state, _list_frames
    movie_dir = os.path.join(movies_root, active_movie)
    frames = _list_frames(movie_dir)
    total = len(frames)
    if total == 0:
        await ctx.send(f"No frames found in `data/movies/{active_movie}/`.")
        return

    state = _load_state(movie_dir)
    idx = state.get("frame_index", 0) % total

    try:
        n = int(amount)
    except ValueError:
        n = 1

    if action in ("skip", "next", "forward"):
        new_idx = (idx + n) % total
        _save_state(movie_dir, {"frame_index": new_idx})
        await ctx.send(f"⏭️ Skipped {n} frame(s) → frame {new_idx + 1}/{total}")
    elif action in ("back", "prev", "previous"):
        new_idx = (idx - n) % total
        _save_state(movie_dir, {"frame_index": new_idx})
        await ctx.send(f"⏮️ Went back {n} frame(s) → frame {new_idx + 1}/{total}")
    elif action == "goto":
        new_idx = max(0, min(n - 1, total - 1))
        _save_state(movie_dir, {"frame_index": new_idx})
        await ctx.send(f"➡️ Jumped to frame {new_idx + 1}/{total}")
    elif action == "reset":
        _save_state(movie_dir, {"frame_index": 0})
        await ctx.send(f"🔄 Reset to frame 1/{total}")
    elif action in ("status", "info", None):
        await ctx.send(
            f"🎬 **{active_movie}** — frame {idx + 1}/{total}\n"
            f"Use `!movie skip N`, `!movie back N`, `!movie goto N`, or `!movie reset`."
        )
        return
    else:
        await ctx.send(f"Unknown action `{action}`. Use: skip, back, goto, reset, status.")
        return

    # Refresh display to show new frame
    success, output = await run_main()
    if success:
        await send_display_image(ctx.channel, load_config())


# Commands blocked from remote execution
_RUN_BLOCKLIST = [
    "rm -rf /", "rm -rf ~", ":(){:|:&};:", "mkfs", "dd if=",
    "> /dev/sda", "chmod -R 777 /", "shutdown", "reboot",
    "halt", "poweroff", "init 0", "init 6",
]

@channel_guard()
async def cmd_run(ctx: commands.Context, *, command: str = None):
    """Execute a shell command on the Pi and display the output on the e-ink screen."""
    if not command:
        await ctx.send("Usage: `!run <command>`\nExample: `!run df -h`")
        return

    # Basic safety check
    cmd_lower = command.lower()
    for blocked in _RUN_BLOCKLIST:
        if blocked in cmd_lower:
            await ctx.send(f"❌ Command blocked: `{blocked}`")
            return

    msg = await ctx.send(f"Running: `{command}`...")

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=ROOT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            await msg.edit(content=f"❌ `{command}` — timed out after 30 seconds.")
            return

        output   = stdout.decode(errors="replace").strip()
        exit_code = proc.returncode
    except Exception as e:
        await msg.edit(content=f"❌ Failed to run command: {e}")
        return

    # Store result in terminal state and switch display to terminal module
    from modules.terminal import save_entry
    save_entry(command, output, exit_code)
    update_bot_state("active_module", "terminal")

    # Discord embed with output
    status = "✅" if exit_code == 0 else f"❌ exit {exit_code}"
    embed = discord.Embed(
        title=f"{status}  `{command[:60]}`",
        color=discord.Color.green() if exit_code == 0 else discord.Color.red(),
    )
    display_output = output[:1800] if output else "(no output)"
    embed.add_field(name="Output", value=f"```\n{display_output}\n```", inline=False)
    await msg.edit(content=None, embed=embed)

    # Refresh display
    success, _ = await run_main()
    if success:
        await send_display_image(ctx.channel, load_config())


@channel_guard()
async def cmd_set(ctx: commands.Context, key: str = None, *, value: str = None):
    """Update a config value and refresh the display."""
    if key is None:
        await ctx.send(
            "Usage: `!set <key> [value]`\n"
            "Examples:\n"
            "```\n"
            "!set station KOHX\n"
            "!set radar_mode panel\n"
            "!set flight_radar.map_zoom 10\n"
            "!set module_cycler.modules weather,nasa_apod,moon_phase\n"
            "!set franklin_cam\n"
            "```\n"
            "Run `!modules` to see all configurable args."
        )
        return

    cfg = load_config()
    description = None

    # Special: station lookup — write full dict so config stays valid
    if key in ("station", "weather.station"):
        station_dict = find_station(value, cfg)
        if station_dict is None:
            await ctx.send(
                f"Station `{value.upper()}` not found in the stations list.\n"
                "Check config.yml `stations:` for valid codes."
            )
            return
        update_bot_state("station", station_dict)
        update_bot_state("active_module", "weather")
        description = f"**active_module** → `weather`\n**station** → `{station_dict['name']}` ({station_dict.get('location', '')})"

    # Special: !set <module_name> [value] — switch module + set primary arg if defined
    elif key in ALL_MODULES:
        update_bot_state("active_module", key)
        primary = MODULE_PRIMARY_ARG.get(key)
        if primary and value:
            cast = cast_value(value)
            update_bot_state(primary, cast)
            description = f"**active_module** → `{key}`\n**{primary}** → `{cast}`"
        else:
            description = f"**active_module** → `{key}`"

    # Special: module_cycler.modules — split by comma into a list
    elif key == "module_cycler.modules":
        modules_list = [m.strip() for m in value.split(",") if m.strip()]
        unknown = [m for m in modules_list if m not in ALL_MODULES]
        if unknown:
            await ctx.send(f"Unknown module(s): {', '.join(f'`{m}`' for m in unknown)}\nRun `!modules` for valid names.")
            return
        update_bot_state("module_cycler.modules", modules_list)
        description = f"**module_cycler.modules** → `{', '.join(modules_list)}`"

    # General key (dot notation, auto-cast)
    else:
        if value is None:
            await ctx.send(f"Usage: `!set {key} <value>`")
            return
        cast = cast_value(value)
        update_bot_state(key, cast)
        description = f"**{key}** → `{cast}`"

    live_cfg = load_config()
    active = live_cfg.get("active_module", "?")
    msg = await ctx.send(f"Config updated — refreshing display ({active})...")

    success, output = await run_main()

    if success:
        next_upd = _next_update_str(active, live_cfg, MODULE_INTERVALS, 21600)
        embed = discord.Embed(
            title=f"Display refreshed — {active}",
            description=f"{description}\n{next_upd}",
            color=discord.Color.green(),
        )
        embed.add_field(name="Output", value=f"```{output[:900]}```", inline=False)
    else:
        embed = discord.Embed(
            title="Config updated — refresh failed",
            description=description,
            color=discord.Color.red(),
        )
        embed.add_field(name="Error", value=f"```{output[:900]}```", inline=False)

    await msg.edit(content=None, embed=embed)

    if success:
        await send_display_image(ctx.channel, load_config())


@channel_guard()
async def cmd_refresh(ctx: commands.Context):
    """Force a display refresh with the current active module."""
    cfg = load_config()
    active = cfg.get("active_module", "?")
    msg = await ctx.send(f"Refreshing display ({active})...")

    success, output = await run_main()

    if success:
        embed = discord.Embed(
            title=f"Display refreshed — {active}",
            color=discord.Color.green(),
        )
        embed.add_field(name="Output", value=f"```{output[:900]}```", inline=False)
    else:
        embed = discord.Embed(
            title="Refresh failed",
            color=discord.Color.red(),
        )
        embed.add_field(name="Error", value=f"```{output[:900]}```", inline=False)

    await msg.edit(content=None, embed=embed)


@channel_guard()
async def cmd_status(ctx: commands.Context):
    """Show the current display state from config.yml."""
    cfg = load_config()
    active = cfg.get("active_module", "unknown")

    embed = discord.Embed(title="E-Ink Display Status", color=discord.Color.og_blurple())
    embed.add_field(name="Active module", value=f"`{active}`", inline=True)
    embed.add_field(name="Update interval", value=f"{cfg.get('update_interval', '?')}s", inline=True)

    if active in ("weather", "module_cycler"):
        station = cfg.get("station", {})
        if isinstance(station, dict):
            embed.add_field(
                name="Station",
                value=f"`{station.get('name', '?')}` — {station.get('location', '')}",
                inline=False,
            )
        embed.add_field(name="Radar mode", value=f"`{cfg.get('radar_mode', '?')}`", inline=True)
        embed.add_field(name="Panel width", value=f"{cfg.get('panel_width', '?')}px", inline=True)

    if active == "module_cycler":
        cycle_modules = cfg.get("module_cycler", {}).get("modules", [])
        embed.add_field(name="Cycle list", value=", ".join(f"`{m}`" for m in cycle_modules), inline=False)

        # Show last-run module from state file
        state_path = os.path.join(ROOT, cfg.get("module_cycler", {}).get("state_file", "data/cycler_state.json"))
        if os.path.exists(state_path):
            import json
            with open(state_path) as f:
                state = json.load(f)
            last = state.get("last_module")
            if last:
                embed.add_field(name="Last cycled", value=f"`{last}`", inline=True)

    loc = cfg.get("forecast_location", {})
    if loc:
        embed.add_field(
            name="Forecast location",
            value=f"{loc.get('name', '')} ({loc.get('latitude', '?')}, {loc.get('longitude', '?')})",
            inline=False,
        )

    await ctx.send(embed=embed)


@channel_guard()
async def cmd_modules(ctx: commands.Context):
    """List all available modules with numbers; keep typing numbers to switch."""
    numbered = "\n".join(f"{i+1:2}. {m}" for i, m in enumerate(ALL_MODULES))
    await ctx.send(
        f"**Available Modules** — type a number to switch. Session times out after 60s of inactivity.\n```\n{numbered}\n```"
    )

    def _check(m):
        return m.author == ctx.author and m.channel == ctx.channel and m.content.strip().isdigit()

    while True:
        try:
            reply = await bot.wait_for("message", check=_check, timeout=60)
        except asyncio.TimeoutError:
            await ctx.send("Module selection session ended.")
            return

        choice = int(reply.content.strip())
        if not (1 <= choice <= len(ALL_MODULES)):
            await ctx.send(f"Number must be 1–{len(ALL_MODULES)}. Try again.")
            continue

        module = ALL_MODULES[choice - 1]

        cfg = load_config()
        missing_required, _ = _check_module_config(module, cfg)
        if missing_required:
            lines = [f"`!set {k} <value>` — {desc}" for k, desc in missing_required]
            await ctx.send(embed=discord.Embed(
                title=f"Cannot display `{module}` — required config missing",
                description="\n".join(lines),
                color=discord.Color.red(),
            ))
            continue

        update_bot_state("active_module", module)
        msg = await ctx.send(f"Switching display to **{module}**...")
        success, output = await run_main()

        if success:
            next_upd = _next_update_str(module, load_config(), MODULE_INTERVALS, 21600)
            embed = discord.Embed(title=f"Display updated — {module}", description=next_upd, color=discord.Color.green())
            embed.add_field(name="Output", value=f"```{output[:900]}```", inline=False)
        else:
            embed = discord.Embed(title="Display update failed", color=discord.Color.red())
            embed.add_field(name="Error", value=f"```{output[:900]}```", inline=False)

        await msg.edit(content=None, embed=embed)
        if success:
            await send_display_image(ctx.channel, load_config())


@channel_guard()
async def cmd_help_display(ctx: commands.Context):
    """Show command reference."""
    prefix = bot.command_prefix
    embed = discord.Embed(title="E-Ink Display Bot — Commands", color=discord.Color.og_blurple())
    embed.add_field(name=f"{prefix}display <module>", value="Switch active module and refresh. Shows configurable options after switching.", inline=False)
    embed.add_field(name=f"{prefix}text <message>", value="Display a custom text message on the screen", inline=False)
    embed.add_field(name=f"{prefix}questions [minutes]", value="Show rotating questions — asks for interval if not provided", inline=False)
    embed.add_field(name=f"{prefix}run <command>", value="Execute a shell command on the Pi and show output on the display", inline=False)
    embed.add_field(name=f"{prefix}display interesting_fact", value="Show rotating facts — updates every hour by default", inline=False)
    embed.add_field(name=f"{prefix}set <key> <value>", value="Update a config value (dot notation). Does not auto-refresh.", inline=False)
    embed.add_field(name=f"{prefix}refresh", value="Force display refresh with current module", inline=False)
    embed.add_field(name=f"{prefix}status", value="Show current display state", inline=False)
    embed.add_field(name=f"{prefix}modules", value="List all modules and their configurable args", inline=False)
    embed.add_field(name=f"{prefix}video <url>", value="Download a YouTube video, extract frames, and display as movie slideshow. Prompts for name, interval, start offset, and fill mode.", inline=False)
    embed.add_field(name=f"{prefix}movie skip/back/goto/reset/status", value="Control the movie slideshow — skip frames, jump to a frame, or restart", inline=False)
    embed.add_field(
        name="Examples",
        value=(
            f"```\n"
            f"{prefix}display weather\n"
            f"{prefix}set station KOHX\n"
            f"{prefix}set radar_mode panel\n"
            f"{prefix}set flight_radar.map_zoom 10\n"
            f"{prefix}set module_cycler.modules weather,nasa_apod,moon_phase\n"
            f"{prefix}video https://youtube.com/watch?v=...\n"
            f"{prefix}movie skip 5\n"
            f"{prefix}refresh\n"
            f"```"
        ),
        inline=False,
    )
    await ctx.send(embed=embed)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


# Default intervals per module (seconds). Config can override via
# <module>.update_interval or the top-level update_interval fallback.
MODULE_INTERVALS: dict[str, int] = {
    "weather":          1800,   # 30 min — radar updates frequently
    "franklin_cam":     300,    # 5 min  — live camera
    "parking_garage":   600,    # 10 min
    "flight_radar":     900,    # 15 min
    "news_headlines":   3600,   # 1 hour
    "interesting_fact": 3600,   # 1 hour
    "questions":        900,    # 15 min (overridden by questions.interval_minutes)
    "moon_phase":       3600,   # 1 hour
    "quote_of_day":     86400,  # 24 hours
    "on_this_day":      86400,  # 24 hours
    "saint_of_day":     86400,  # 24 hours
    "chess_puzzle":     86400,  # 24 hours — daily puzzle
    "sudoku_puzzle":    86400,  # 24 hours
    "poem_of_day":      86400,  # 24 hours
    "nasa_apod":        86400,  # 24 hours
    "art_of_day":       86400,  # 24 hours
    "wiki_image":       86400,  # 24 hours
    "claude_news":      18000,  # 5 hours
    "brain_status":     1800,   # 30 min
    "module_cycler":    1800,   # 30 min
    "crypto_market":    21600,  # 6 hours
}


def main():
    global bot, ALLOWED_CHANNEL

    cfg = load_config()
    discord_cfg = cfg.get("discord", {})

    # Env vars take priority over config.yml
    token = os.environ.get("DISCORD_BOT_TOKEN") or discord_cfg.get("bot_token", "")
    channel_id = os.environ.get("DISCORD_CHANNEL_ID") or discord_cfg.get("channel_id", 0)
    prefix = os.environ.get("DISCORD_PREFIX") or discord_cfg.get("command_prefix", "!")

    if not token:
        print("ERROR: DISCORD_BOT_TOKEN not set in .env or config.yml discord.bot_token")
        sys.exit(1)

    ALLOWED_CHANNEL = int(channel_id) if channel_id else 0
    bot = make_bot(prefix)

    GLOBAL_FALLBACK = int(cfg.get("update_interval", 21600))

    # ---------------------------------------------------------------------------
    # Scheduled auto-refresh loop
    # ---------------------------------------------------------------------------

    def _module_interval(active: str) -> int:
        """Return refresh interval (seconds) for the active module."""
        live_cfg = load_config()
        module_cfg = live_cfg.get(active, {})
        if isinstance(module_cfg, dict) and "update_interval" in module_cfg:
            return int(module_cfg["update_interval"])
        return MODULE_INTERVALS.get(active, GLOBAL_FALLBACK)

    _last_refresh: list[float] = [0.0]   # mutable container so the closure can write it

    # Modules that are purely static — never auto-refresh them
    NO_AUTO_REFRESH = {"text", "qrcode_display", "terminal"}

    @tasks.loop(seconds=60)
    async def auto_refresh():
        """Poll every minute; fire when the active module's interval has elapsed."""
        import time
        cfg_now = load_config()
        active = cfg_now.get("active_module", "?")
        if active in NO_AUTO_REFRESH:
            return
        interval = _module_interval(active)
        elapsed = time.time() - _last_refresh[0]
        if elapsed < interval:
            return

        print(f"[auto_refresh] {active} — {elapsed:.0f}s elapsed >= {interval}s interval")
        _last_refresh[0] = time.time()
        success, output = await run_main()

        # Skip Discord notification if nothing actually changed
        display_updated = success and (
            "unchanged" not in output.lower() and
            "no output" not in output.lower()
        )

        if display_updated and ALLOWED_CHANNEL:
            channel = bot.get_channel(ALLOWED_CHANNEL)
            if channel:
                embed = discord.Embed(
                    title=f"✅ Scheduled refresh — {active}",
                    description=f"Interval: every {interval // 60} min",
                    color=discord.Color.green(),
                )
                await channel.send(embed=embed)
        elif not success and ALLOWED_CHANNEL:
            channel = bot.get_channel(ALLOWED_CHANNEL)
            if channel:
                embed = discord.Embed(
                    title=f"❌ Scheduled refresh failed — {active}",
                    color=discord.Color.red(),
                )
                embed.add_field(name="Error", value=f"```{output[:800]}```", inline=False)
                await channel.send(embed=embed)

    @auto_refresh.before_loop
    async def before_auto_refresh():
        await bot.wait_until_ready()

    # Register commands
    bot.command(name="display")(cmd_display)
    bot.command(name="text")(cmd_text)
    bot.command(name="questions")(cmd_questions)
    bot.command(name="run")(cmd_run)
    bot.command(name="set")(cmd_set)
    bot.command(name="refresh")(cmd_refresh)
    bot.command(name="status")(cmd_status)
    bot.command(name="modules")(cmd_modules)
    bot.command(name="help_display")(cmd_help_display)
    bot.command(name="help")(cmd_help_display)
    bot.command(name="video")(cmd_video)
    bot.command(name="movie")(cmd_movie)

    @bot.event
    async def on_ready():
        # Seed bot_state.json from config.yml so all module keys are present.
        # Existing bot_state overrides are preserved on top; safe=True drops any
        # stale scalar that would overwrite a config dict (e.g. text: "msg").
        with open(CONFIG_PATH) as f:
            base = yaml.safe_load(f) or {}
        existing = load_bot_state()
        merged = _deep_merge(base, existing, safe=True)
        with open(BOT_STATE_PATH, "w") as f:
            json.dump(merged, f, indent=2)
        auto_refresh.start()
        print(f"Discord bot ready — logged in as {bot.user} (channel_id={ALLOWED_CHANNEL or 'any'}, per-module auto-refresh active)")
        if ALLOWED_CHANNEL:
            channel = bot.get_channel(ALLOWED_CHANNEL)
            if channel:
                active_module = merged.get("active_module", "?")
                embed = discord.Embed(
                    title="🟢 E-ink Bot Online",
                    description=f"Active module: **{active_module}**\nAuto-refresh active.",
                    color=discord.Color.green(),
                )
                await channel.send(embed=embed)

    @bot.event
    async def on_command_error(ctx, error):
        if isinstance(error, commands.CheckFailure):
            return  # silently ignore commands in wrong channels
        if isinstance(error, commands.CommandNotFound):
            return
        await ctx.send(f"Error: {error}")

    @bot.event
    async def on_message(message):
        if message.author.bot:
            return
        if ALLOWED_CHANNEL and message.channel.id != ALLOWED_CHANNEL:
            return
        if message.content.strip().lower() == "help":
            ctx = await bot.get_context(message)
            await cmd_help_display(ctx)
            return
        await bot.process_commands(message)

    bot.run(token)


if __name__ == "__main__":
    main()
