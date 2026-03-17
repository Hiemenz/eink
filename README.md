# E-Ink Display

A Raspberry Pi e-ink display system built around a **Waveshare 7.5" V2 (800×480)** e-paper screen. Content is generated as 800×480 BMP images by a set of pluggable modules and pushed to the hardware. Everything is controlled via a Discord bot — no SSH required once deployed.

---

## Hardware

| Component | Model |
|---|---|
| Display | Waveshare 7.5" V2 (800×480, black/white) |
| Controller | Raspberry Pi (any model with GPIO/SPI) |
| Alt display | Waveshare 7.3" ACeP 7-Color (configure via `display_model: epd7in3f`) |

---

## Quick Start

```bash
# Install dependencies
poetry install

# Run once (generates image + pushes to hardware on Pi)
poetry run python main.py

# Start the Discord bot (handles scheduling + commands)
poetry run python discord_bot.py
```

On **macOS** the hardware push is skipped — `main.py` prints the output path instead.

---

## Architecture

```
eink/
├── main.py              # Dispatcher: loads config, calls active module's generate(), pushes to display
├── discord_bot.py       # Discord bot: commands + per-module auto-refresh scheduler
├── display.py           # Hardware abstraction — file-locked EPD driver (Linux only)
├── utils.py             # Shared helpers: MODULE_MAP, get_font(), get_logger(), validate_config()
├── config.yml           # All configuration (hardware, modules, Discord, intervals)
├── bot_state.json       # Runtime overrides written by the Discord bot (git-ignored)
│
├── modules/             # One file per content module, each exports generate(config)
│   ├── weather.py           # NWS radar image (crop / fit / panel modes)
│   ├── text_display.py      # Custom text message or CSV-driven facts
│   ├── questions.py         # Rotating questions from CSV, configurable interval
│   ├── interesting_fact.py  # Rotating facts from CSV, 1-hour default interval
│   ├── claude_news.py       # 3 most recent Claude Code feature releases
│   ├── chess_puzzle.py      # Lichess Daily Puzzle with board diagram
│   ├── sudoku_puzzle.py     # Generated Sudoku grid
│   ├── moon_phase.py        # Current moon phase diagram
│   ├── nasa_apod.py         # NASA Astronomy Picture of the Day
│   ├── art_of_day.py        # Public-domain artwork of the day
│   ├── wiki_image.py        # Wikipedia Picture of the Day
│   ├── saint_of_day.py      # Saint of the Day (scraped, cached daily)
│   ├── on_this_day.py       # Historical events from Wikipedia
│   ├── quote_of_day.py      # Inspirational quote
│   ├── poem_of_day.py       # Poem of the day
│   ├── news_headlines.py    # Top news headlines
│   ├── flight_radar.py      # Live flight map centered on forecast_location
│   ├── franklin_cam.py      # Live Five Points Downtown Franklin traffic camera
│   ├── parking_garage.py    # Downtown Franklin parking availability
│   ├── movie_slideshow.py   # Sequential image frame player
│   ├── module_cycler.py     # Cycles through a configured list of modules
│   └── brain_status.py      # AI brain / knowledge base status display
│
├── data/
│   ├── questions/           # CSVs (topic, question columns)
│   ├── movies/              # Movie frame directories for slideshow
│   └── *.json               # Module cache files (auto-generated)
│
├── images/                  # Generated BMP output files (auto-generated)
├── radar/                   # Cached radar images and state (auto-generated)
├── waveshare_epd/           # Waveshare driver library
└── server/                  # Flask web dashboard (port 5000)
```

### How it works

1. **`main.py`** reads `active_module` from `config.yml`, merges in any Discord overrides from `bot_state.json`, imports the matching module from `MODULE_MAP` in `utils.py`, calls `generate(config)`, and passes the returned BMP path to `display.py`.

2. **`discord_bot.py`** is the primary interface. It runs continuously, handles user commands, and owns the refresh schedule. When a command changes the active module or any config key, it writes to `bot_state.json` then spawns `main.py` as a subprocess to regenerate and push.

3. **`display.py`** uses `fcntl` file locking so that concurrent refresh requests (cron + Discord command) never corrupt the EPD hardware. If a display operation is already in progress, the second caller logs a skip and returns cleanly.

---

## Modules

Each module is a standalone Python file in `modules/` that exports a single function:

```python
def generate(config: dict) -> str:
    """Generate image. Return absolute or relative output path."""
```

The module reads its own config section (`config.get("module_name", {})`), fetches any data it needs, renders an 800×480 RGB image with Pillow, saves it, and returns the path.

### Adding a new module

1. Create `modules/my_module.py` with a `generate(config)` function.
2. Register it in `utils.MODULE_MAP`:
   ```python
   "my_module": "modules.my_module",
   ```
3. Add a config section to `config.yml`:
   ```yaml
   my_module:
     output_path: images/my_module.bmp
     update_interval: 3600   # optional — overrides the bot's default
   ```
4. Add it to `ALL_MODULES` and `MODULE_ARGS` in `discord_bot.py`.

---

## Configuration

All settings live in `config.yml`. The Discord bot writes runtime overrides to `bot_state.json` (same directory, git-ignored). On startup `main.py` deep-merges both files — `bot_state.json` wins.

### Key top-level keys

| Key | Description |
|---|---|
| `active_module` | Which module to display |
| `display_model` | EPD driver: `epd7in5_V2` or `epd7in3f` |
| `update_interval` | Fallback refresh interval in seconds (default 21600 = 6 h) |
| `radar_mode` | Weather layout: `crop` / `fit` / `panel` |
| `station` | Active NEXRAD radar station (YAML anchor) |
| `forecast_location` | Lat/lon used by weather panel, flight radar, and conditions fetch |

### Per-module refresh intervals

The Discord bot schedules each module independently. Defaults (overridable via `<module>.update_interval` in `config.yml`):

| Module | Default |
|---|---|
| `franklin_cam` | 5 min |
| `parking_garage` | 10 min |
| `questions` | 15 min |
| `flight_radar` | 15 min |
| `weather`, `brain_status` | 30 min |
| `news_headlines`, `moon_phase`, `interesting_fact`, `claude_news` | 1 hr |
| `chess_puzzle`, `nasa_apod`, `poem_of_day`, daily modules | 24 hr |

---

## Discord Bot

The bot is the primary control interface. Run it once on the Pi and leave it running.

```bash
poetry run python discord_bot.py
```

Set credentials in `.env` (or `config.yml discord:` section):

```
DISCORD_BOT_TOKEN=your_token_here
DISCORD_CHANNEL_ID=123456789
```

### Commands

| Command | Description |
|---|---|
| `!display` | Numbered module menu — reply with a number to switch |
| `!display <module>` | Switch directly to a named module |
| `!text <message>` | Display a custom text message |
| `!questions [minutes]` | Show rotating questions; asks for interval if not provided |
| `!set <key> [value]` | Update any config value (dot notation). `!set franklin_cam` switches with no args |
| `!refresh` | Force refresh with the current module |
| `!status` | Show current module, station, and location |
| `!modules` | List all modules and their configurable options |
| `!help` or `help` | Show this command reference |

The bot also posts an embed + display image preview to the channel on every scheduled auto-refresh.

---

## Weather Module

Three radar display modes controlled by `radar_mode` in `config.yml`:

| Mode | Description |
|---|---|
| `crop` | Scale-fill the full 800×480 canvas |
| `fit` | Letterbox with neighboring station strips on the edges |
| `panel` | Crop radar to the left portion; right panel shows current conditions, temperature, wind, and a QR code link |

**Panel mode** fetches live conditions from Open-Meteo (no API key required) and caches them for 5 minutes.

---

## Development vs Pi

| | macOS (dev) | Raspberry Pi |
|---|---|---|
| Image generation | ✅ | ✅ |
| Hardware push | ❌ (skipped) | ✅ |
| Font | `/Library/Fonts/Arial Unicode.ttf` | `/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf` + fallbacks |
| Log file | stderr only | `/var/log/eink.log` (rotating, 5 MB) |

---

## Dependencies

| Package | Purpose |
|---|---|
| `pillow` | Image generation |
| `requests` | HTTP fetching |
| `pyyaml` | Config parsing |
| `discord.py` | Discord bot |
| `python-dotenv` | `.env` support |
| `flask` | Web dashboard |
| `beautifulsoup4` + `lxml` | HTML scraping (saint module) |
| `staticmap` + `geopy` | Flight radar map tiles |
| `pyarrow` + `pandas` | Parking garage history |
| `astral` | Sunrise/sunset for moon phase |
| `qrcode` | Radar panel QR code |
| `spidev` + `rpi-gpio` | SPI/GPIO hardware (Linux only) |
