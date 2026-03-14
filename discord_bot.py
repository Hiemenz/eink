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
from discord.ext import commands
from PIL import Image

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
]

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
        "module_cycler.modules": "Comma-separated module list (e.g. weather,nasa_apod,moon_phase)",
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


def _deep_merge(base: dict, overrides: dict) -> dict:
    """Recursively merge overrides into base without modifying either."""
    result = dict(base)
    for k, v in overrides.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
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
    return _deep_merge(cfg, load_bot_state())


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
        target = target.setdefault(part, {})
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

    path_map = {
        "weather":         os.path.join(ROOT, "radar", f"eink_quantized_display_{station_name}.bmp"),
        "text":            os.path.join(ROOT, cfg.get("text", {}).get("output_path", "images/text_display.bmp")),
        "saint_of_day":    os.path.join(ROOT, cfg.get("saint_of_day", {}).get("output_path", "images/saint_display.bmp")),
        "wiki_image":      os.path.join(ROOT, cfg.get("wiki_image", {}).get("output_path", "images/wiki_display.bmp")),
        "movie_slideshow": os.path.join(ROOT, cfg.get("movie_slideshow", {}).get("output_path", "images/movie_display.bmp")),
        "nasa_apod":       os.path.join(ROOT, cfg.get("nasa_apod", {}).get("output_path", "images/nasa_apod.bmp")),
        "quote_of_day":    os.path.join(ROOT, cfg.get("quote_of_day", {}).get("output_path", "images/quote_display.bmp")),
        "on_this_day":     os.path.join(ROOT, cfg.get("on_this_day", {}).get("output_path", "images/onthisday_display.bmp")),
        "moon_phase":      os.path.join(ROOT, cfg.get("moon_phase", {}).get("output_path", "images/moon_display.bmp")),
        "art_of_day":      os.path.join(ROOT, cfg.get("art_of_day", {}).get("output_path", "images/art_display.bmp")),
        "chess_puzzle":    os.path.join(ROOT, cfg.get("chess_puzzle", {}).get("output_path", "images/chess_display.bmp")),
        "sudoku_puzzle":   os.path.join(ROOT, cfg.get("sudoku_puzzle", {}).get("output_path", "images/sudoku_display.bmp")),
        "poem_of_day":     os.path.join(ROOT, cfg.get("poem_of_day", {}).get("output_path", "images/poem_display.bmp")),
        "news_headlines":  os.path.join(ROOT, cfg.get("news_headlines", {}).get("output_path", "images/news_display.bmp")),
        "flight_radar":    os.path.join(ROOT, cfg.get("flight_radar", {}).get("output_path", "images/flight_display.bmp")),
        "franklin_cam":    os.path.join(ROOT, cfg.get("franklin_cam", {}).get("output_path", "images/franklin_cam.bmp")),
        "parking_garage":  os.path.join(ROOT, cfg.get("parking_garage", {}).get("output_path", "images/parking_display.bmp")),
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
        await ctx.send("Usage: `!display <module>`  — try `!modules` for the full list.")
        return

    if module not in ALL_MODULES:
        embed = discord.Embed(
            title="Unknown module",
            description=f"`{module}` is not a valid module.\nRun `!modules` to see available options.",
            color=discord.Color.red(),
        )
        await ctx.send(embed=embed)
        return

    update_bot_state("active_module", module)

    msg = await ctx.send(f"Switching display to **{module}**...")

    success, output = await run_main()

    if success:
        embed = discord.Embed(
            title=f"Display updated — {module}",
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
async def cmd_set(ctx: commands.Context, key: str = None, *, value: str = None):
    """Update a config value and save to config.yml. Does not auto-refresh."""
    if key is None or value is None:
        await ctx.send(
            "Usage: `!set <key> <value>`\n"
            "Examples:\n"
            "```\n"
            "!set station KOHX\n"
            "!set radar_mode panel\n"
            "!set flight_radar.map_zoom 10\n"
            "!set module_cycler.modules weather,nasa_apod,moon_phase\n"
            "```\n"
            "Run `!modules` to see all configurable args."
        )
        return

    cfg = load_config()

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
        embed = discord.Embed(
            title="Config updated",
            description=f"**station** → `{station_dict['name']}` ({station_dict.get('location', '')})",
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed)
        return

    # Special: module_cycler.modules — split by comma into a list
    if key == "module_cycler.modules":
        modules_list = [m.strip() for m in value.split(",") if m.strip()]
        unknown = [m for m in modules_list if m not in ALL_MODULES]
        if unknown:
            await ctx.send(f"Unknown module(s): {', '.join(f'`{m}`' for m in unknown)}\nRun `!modules` for valid names.")
            return
        update_bot_state("module_cycler.modules", modules_list)
        embed = discord.Embed(
            title="Config updated",
            description=f"**module_cycler.modules** → `{', '.join(modules_list)}`",
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed)
        return

    # General key (dot notation, auto-cast)
    cast = cast_value(value)
    update_bot_state(key, cast)

    embed = discord.Embed(
        title="Config updated",
        description=f"**{key}** → `{cast}`",
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="Run !refresh to push changes to the display.")
    await ctx.send(embed=embed)


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
    """List all available modules and their configurable args."""
    embed = discord.Embed(
        title="Available Modules",
        description="Use `!display <module>` to switch, `!set <key> <value>` to configure.",
        color=discord.Color.og_blurple(),
    )

    for module in ALL_MODULES:
        args = MODULE_ARGS.get(module)
        if args:
            lines = [f"`{k}` — {v}" for k, v in args.items()]
            embed.add_field(name=module, value="\n".join(lines), inline=False)
        else:
            embed.add_field(name=module, value="_no configurable args_", inline=False)

    await ctx.send(embed=embed)


@channel_guard()
async def cmd_help_display(ctx: commands.Context):
    """Show command reference."""
    prefix = bot.command_prefix
    embed = discord.Embed(title="E-Ink Display Bot — Commands", color=discord.Color.og_blurple())
    embed.add_field(name=f"{prefix}display <module>", value="Switch active module and refresh. Shows configurable options after switching.", inline=False)
    embed.add_field(name=f"{prefix}text <message>", value="Display a custom text message on the screen", inline=False)
    embed.add_field(name=f"{prefix}set <key> <value>", value="Update a config value (dot notation). Does not auto-refresh.", inline=False)
    embed.add_field(name=f"{prefix}refresh", value="Force display refresh with current module", inline=False)
    embed.add_field(name=f"{prefix}status", value="Show current display state", inline=False)
    embed.add_field(name=f"{prefix}modules", value="List all modules and their configurable args", inline=False)
    embed.add_field(
        name="Examples",
        value=(
            f"```\n"
            f"{prefix}display weather\n"
            f"{prefix}set station KOHX\n"
            f"{prefix}set radar_mode panel\n"
            f"{prefix}set flight_radar.map_zoom 10\n"
            f"{prefix}set module_cycler.modules weather,nasa_apod,moon_phase\n"
            f"{prefix}refresh\n"
            f"```"
        ),
        inline=False,
    )
    await ctx.send(embed=embed)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    global bot, ALLOWED_CHANNEL

    cfg = load_config()
    discord_cfg = cfg.get("discord", {})
    token = discord_cfg.get("bot_token", "")
    channel_id = discord_cfg.get("channel_id", 0)
    prefix = discord_cfg.get("command_prefix", "!")

    if not token:
        print("ERROR: discord.bot_token is not set in config.yml")
        sys.exit(1)

    ALLOWED_CHANNEL = int(channel_id) if channel_id else 0
    bot = make_bot(prefix)

    # Register commands
    bot.command(name="display")(cmd_display)
    bot.command(name="text")(cmd_text)
    bot.command(name="set")(cmd_set)
    bot.command(name="refresh")(cmd_refresh)
    bot.command(name="status")(cmd_status)
    bot.command(name="modules")(cmd_modules)
    bot.command(name="help_display")(cmd_help_display)

    @bot.event
    async def on_ready():
        print(f"Discord bot ready — logged in as {bot.user} (channel_id={ALLOWED_CHANNEL or 'any'})")

    @bot.event
    async def on_command_error(ctx, error):
        if isinstance(error, commands.CheckFailure):
            return  # silently ignore commands in wrong channels
        if isinstance(error, commands.CommandNotFound):
            return
        await ctx.send(f"Error: {error}")

    bot.run(token)


if __name__ == "__main__":
    main()
