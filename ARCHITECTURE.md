# Architecture

This document describes how the e-ink display system is built and how its parts fit together.

---

## System Overview

```
                          ┌─────────────────────────────────┐
                          │         config.yml              │
                          │   +  bot_state.json (runtime)   │
                          └─────────────┬───────────────────┘
                                        │ deep-merged on startup
                                        ▼
┌──────────────┐  subprocess   ┌────────────────┐   generate()   ┌─────────────────┐
│ discord_bot  │──────────────▶│    main.py     │───────────────▶│  modules/<name> │
│   .py        │               │  (dispatcher)  │                │  .generate(cfg) │
└──────┬───────┘               └────────┬───────┘                └────────┬────────┘
       │                                │ BMP path                        │
       │  bot_state.json                ▼                                 │ BMP file
       │  (runtime overrides)  ┌────────────────┐                         │
       │                       │   display.py   │◀────────────────────────┘
       │                       │  (EPD driver)  │
       │                       └────────────────┘
       │
       └──────────────────────────────────────────────────────────────────▶ Discord channel
                                                                           (status embeds)

┌──────────────┐  HTTP  ┌──────────────────────────┐
│  Browser     │───────▶│  server/app.py (Flask)   │
│  (LAN only)  │        │  port 5000               │
└──────────────┘        └──────────────────────────┘

┌──────────────────────────────────────────────────┐
│  ai_brain/  (optional autonomous reasoning loop) │
│  brain.py → orchestrator → agents → skills       │
└──────────────────────────────────────────────────┘
```

---

## Component Descriptions

### `main.py` — Dispatcher

The single entry point for rendering a frame. It:

1. Loads `config.yml` and deep-merges `bot_state.json` on top (bot overrides win).
2. Checks the night-sleep window; exits early if active.
3. Looks up the active module in `MODULE_MAP` (from `utils.py`).
4. Calls `module.generate(config)`, which returns a BMP path.
5. MD5-hashes the output. Skips the hardware push if the image is identical to the last push (avoids unnecessary EPD wear).
6. On Linux, calls `display.display_color_image()`. On macOS, logs the path and skips hardware.

`main.py` is stateless — every run is independent. Scheduling is owned by `discord_bot.py`.

---

### `discord_bot.py` — Control Plane

The long-running process that owns:

- **Command handling** — `!display`, `!refresh`, `!set`, `!status`, `!text`, `!modules`, `!help`.
- **Auto-refresh scheduler** — a `discord.ext.tasks` loop that fires at each module's interval (sourced from `MODULE_INTERVALS` in `utils.py`) and spawns `main.py` as a subprocess.
- **Alert auto-override** — polls NWS every 5 min when `alert_auto_override: true`; switches to `weather` on Severe/Extreme alerts and restores the previous module when clear.
- **Runtime state** — writes `bot_state.json` for any config change so that even a cold `main.py` restart picks up the current settings.

The bot posts the generated BMP as a Discord embed after every scheduled or forced refresh.

---

### `display.py` — Hardware Abstraction

Wraps the Waveshare EPD drivers with two safety features:

- **File-level mutex** (`/tmp/eink_display.lock` via `fcntl`) — if two callers race (e.g. a cron job and a Discord command), the second skips rather than corrupting the hardware.
- **Periodic deep-clear** — when `full_clear_interval > 0` and the driver is `epd7in3f`, runs an extra double-clear every N refreshes to prevent 7-color ACeP ghosting.

Two drivers are supported, selected by `display_model` in `config.yml`:

| Driver | Display |
|---|---|
| `epd7in5_V2` | Waveshare 7.5" V2 — 800×480, black/white |
| `epd7in3f` | Waveshare 7.3" ACeP — 800×480, 7-color |

---

### `utils.py` — Shared Globals

Single source of truth for three things:

| Symbol | Purpose |
|---|---|
| `MODULE_MAP` | `{name → "modules.module_name"}` — used by `main.py` to import the right module |
| `MODULE_INTERVALS` | `{name → seconds}` — per-module refresh cadence, read by the bot scheduler |
| `get_font()` | Platform-aware TrueType font loader with fallback chain (macOS → Pi) |
| `get_logger()` | Consistent logger: stderr on all platforms + rotating file on Linux |
| `validate_config()` | Sanity-check required config keys at startup |

---

### `modules/` — Content Modules

Every module is a standalone Python file that exports one function:

```python
def generate(config: dict) -> str:
    """Fetch data, render 800×480 BMP, return output path."""
```

Modules are self-contained. They read their own config section, fetch data (HTTP, filesystem, or computed), render a `PIL.Image`, save it to `images/<module>.bmp`, and return the path.

**Module categories:**

| Category | Modules |
|---|---|
| Weather / Environment | `weather`, `forecast_graph`, `aurora`, `pollen`, `air_quality`, `carbon_intensity`, `river_height` |
| News / Information | `news_headlines`, `claude_news`, `on_this_day`, `quote_of_day`, `poem_of_day`, `word_of_day`, `xkcd`, `interesting_fact`, `questions` |
| Space / Science | `nasa_apod`, `moon_phase`, `iss_tracker`, `earthquakes` |
| Art / Culture | `art_of_day`, `wiki_image`, `saint_of_day`, `now_playing` |
| Games / Puzzles | `chess_puzzle`, `sudoku_puzzle`, `game_of_life` |
| Finance | `stocks`, `crypto_market` |
| Local / Live | `franklin_cam`, `parking_garage`, `flight_radar`, `traffic`, `sports_scores` |
| Utilities | `text_display`, `qrcode_display`, `terminal`, `countdown`, `agenda`, `movie_slideshow` |
| Meta | `module_cycler`, `brain_status` |

**Adding a module** — four steps:

1. Create `modules/my_module.py` with `generate(config) → str`.
2. Register it in `utils.MODULE_MAP`.
3. Add an interval to `utils.MODULE_INTERVALS`.
4. Add a config section to `config.yml` and add the name to `ALL_MODULES` + `MODULE_ARGS` in `discord_bot.py`.

---

### `server/app.py` — Web Dashboard

A Flask app (port 5000) for browser-based control on the local network. Supports:

- Viewing the current config and active module.
- Switching modules and triggering refreshes.
- Managing the movie slideshow (list movies, jump to frame).
- Downloading generated BMP images.

Run with `poetry run python server/app.py`. Not required for normal operation — the Discord bot covers the same actions.

---

### `ai_brain/` — Autonomous Reasoning Loop (Optional)

A self-contained subsystem that runs an LLM-driven agent loop on a configurable interval. Its architecture:

```
brain.py (loop)
  ├── observe_state()   — collect metrics, queue, objectives
  ├── review_memory()   — MemoryStore summarises recent events
  ├── decide()          — LLM (Ollama) produces a JSON action
  ├── act()             — TaskOrchestrator spawns an Agent
  │     ├── ResearchAgent  — web search + summarise
  │     ├── BuilderAgent   — write / modify Python code
  │     ├── OperatorAgent  — shell commands, file management
  │     └── PlannerAgent   — decompose a goal into subtasks
  ├── report()          — post status to Discord via discord_bridge.py
  └── sleep(interval)
```

The brain is independent of the display pipeline. The `brain_status` display module reads its SQLite database (`brain.db`) to show current brain state on the e-ink screen.

---

## Configuration System

```
config.yml          — base configuration (checked into git)
    +
bot_state.json      — runtime overrides written by the Discord bot (git-ignored)
    ║
    ╠══▶ main.py deep-merges both on every run (bot_state wins)
    ╚══▶ server/app.py reads config.yml directly (no bot_state merge)
```

Key top-level config keys:

| Key | Description |
|---|---|
| `active_module` | Module to render |
| `display_model` | EPD driver: `epd7in5_V2` or `epd7in3f` |
| `update_interval` | Fallback refresh interval in seconds (default 21600) |
| `radar_mode` | Weather layout: `crop` / `fit` / `panel` / `seven_color` |
| `radar_source` | `ridge` (NWS NEXRAD) or `rainviewer` (composite tiles) |
| `station` | Active NEXRAD station (YAML anchor from `_presets`) |
| `forecast_location` | Lat/lon used by weather panel, flight radar, AQI |
| `night_sleep` | Optional quiet window — skips all refreshes |
| `full_clear_interval` | Ghosting mitigation for 7-color display (0 = off) |
| `alert_auto_override` | Auto-switch to weather on NWS severe alerts |

---

## Data Flow — Single Refresh

```
cron / !refresh / auto-scheduler
        │
        ▼
  discord_bot.py: subprocess.run(["poetry", "run", "python", "main.py"])
        │
        ▼
  main.py: load_config() → validate_config()
        │
        ▼
  main.py: _in_sleep_window() — early exit if night sleep active
        │
        ▼
  main.py: importlib.import_module(MODULE_MAP[active_module])
        │
        ▼
  module.generate(config) → fetches API / cache → renders PIL image → saves BMP → returns path
        │
        ▼
  main.py: MD5 hash check — skip push if image unchanged
        │
        ▼
  display.display_color_image(path, model, full_clear_interval)
    ├── fcntl lock — skip if already in progress
    ├── epd.init() + epd.Clear()
    ├── epd.display(getbuffer(image))
    └── epd.sleep() — low-power standby
        │
        ▼
  discord_bot.py: post BMP preview embed to Discord channel
```

---

## Caching Strategy

Modules avoid redundant API calls with local JSON cache files in `data/`:

- Cache is written as `data/<module>_cache.json` (path configurable per module).
- On load, modules check cache age against their TTL. If fresh, they skip the HTTP call.
- The `main.py` image-hash check (`images/<module>.bmp.last_hash`) is a second layer: even if the module generates a new image, the hardware push is skipped if the pixels didn't change.

---

## Testing

Tests live in `tests/`, one file per module (`test_<module>.py`). Each test:

- Mocks external HTTP calls (`unittest.mock.patch`) so tests run offline and fast.
- Calls `generate(config)` with a minimal config dict.
- Asserts the returned path exists and the output is a valid BMP at the expected size.

Cross-cutting contract tests in `test_module_contracts.py` verify that every registered module in `MODULE_MAP` satisfies the `generate(config) → str` contract.

Run the suite:

```bash
poetry run pytest tests/
```

---

## Development vs Pi

| | macOS | Raspberry Pi |
|---|---|---|
| Image generation | ✅ | ✅ |
| Hardware push | ❌ skipped | ✅ |
| SPI/GPIO imports | Stubbed | Real |
| Font paths | `/Library/Fonts/Arial Unicode.ttf` | `/usr/share/fonts/truetype/liberation/…` |
| Log output | stderr only | stderr + `/var/log/eink.log` (rotating, 5 MB) |
| AI Brain | Requires Ollama | Requires Ollama |

The `waveshare_epd/` drivers guard hardware-specific calls behind `platform.system() == "Linux"` checks in `display.py`, so the full generation pipeline runs on macOS for development.
