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
4. Calls `module.generate(config)`, which returns a BMP path. An exception here is caught, recorded to `data/health.json` as a failure, and re-raised.
5. MD5-hashes the output. Skips the hardware push if the image is identical to the last push (avoids unnecessary EPD wear).
6. On Linux, calls `display.display_color_image()`, which returns `True`/`False` rather than raising. A `False` push is treated as a failure too — the hash is *not* saved (so the next run retries) and `main.py` exits 1. On macOS, logs the path and skips hardware.
7. Records the run's outcome (success or failure, with the error) to `data/health.json` via `utils.record_health()` — see [Watchdog & Health Checks](#watchdog--health-checks).

`main.py` is stateless — every run is independent. Scheduling is owned by `discord_bot.py`.

---

### `discord_bot.py` — Control Plane

The long-running process that owns:

- **Command handling** — `!display`, `!refresh`, `!set`, `!status`, `!health`, `!text`, `!modules`, `!help`.
- **Auto-refresh scheduler** — a `discord.ext.tasks` loop that fires at each module's interval (sourced from `MODULE_INTERVALS` in `utils.py`) and spawns `main.py` as a subprocess. Each tick's body runs inside a try/except, so an unhandled bug in any one iteration (a flaky API, a bad alert-override response) is logged and the loop retries next minute instead of dying silently.
- **Alert auto-override** — polls NWS every 5 min when `alert_auto_override: true`; switches to `weather` on Severe/Extreme alerts and restores the previous module when clear.
- **Runtime state** — writes `bot_state.json` for any config change so that even a cold `main.py` restart picks up the current settings.

The bot posts the generated BMP as a Discord embed after every scheduled or forced refresh.

---

### `display.py` — Hardware Abstraction

Wraps the Waveshare EPD drivers with three safety features:

- **File-level mutex** (`/tmp/eink_display.lock` via `fcntl`) — if two callers race (e.g. a cron job and a Discord command), the second skips rather than corrupting the hardware.
- **Periodic deep-clear** — when `full_clear_interval > 0` and the driver is `epd7in3f`, runs an extra double-clear every N refreshes to prevent 7-color ACeP ghosting.
- **Never raises, reports success** — hardware exceptions are caught, logged, and turned into a `bool` return value (`True` = image actually reached the panel) instead of propagating, so a flaky panel never crashes a caller's module render. `main.py` uses that return value to distinguish a real push from a silent no-op for health tracking.

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
| News / Information | `news_headlines`, `claude_news`, `on_this_day`, `quote_of_day`, `poem_of_day`, `word_of_day`, `xkcd`, `interesting_fact`, `questions`, `business_idea` |
| Space / Science | `nasa_apod`, `moon_phase`, `iss_tracker`, `earthquakes` |
| Art / Culture | `art_of_day`, `wiki_image`, `saint_of_day`, `now_playing`, `siriusxm_now_playing` |
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
    (exception here → utils.record_health(module, False, error) → re-raised → main.py exits 1)
        │
        ▼
  main.py: MD5 hash check — skip push if image unchanged
        │
        ▼
  display.display_color_image(path, model, full_clear_interval) → returns True/False
    ├── fcntl lock — skip if already in progress (returns False)
    ├── epd.init() + epd.Clear()
    ├── epd.display(getbuffer(image))
    └── epd.sleep() — low-power standby
        │
        ▼
  main.py: utils.record_health(module, pushed, error)  — writes data/health.json
    (pushed == False → hash not saved, so the next run retries → main.py exits 1)
        │
        ▼
  discord_bot.py: post BMP preview embed to Discord channel (or a ❌ failure embed)
```

---

## Caching Strategy

Modules avoid redundant API calls with local JSON cache files in `data/`:

- Cache is written as `data/<module>_cache.json` (path configurable per module).
- On load, modules check cache age against their TTL. If fresh, they skip the HTTP call.
- The `main.py` image-hash check (`images/<module>.bmp.last_hash`) is a second layer: even if the module generates a new image, the hardware push is skipped if the pixels didn't change.

---

## Watchdog & Health Checks

Two independent layers, matching the two ways a "silently broken" module can go unnoticed:

**Per-module health tracking** (`utils.record_health()` / `utils.load_health()`) — every `main.py` run, regardless of what triggered it, writes an entry to `data/health.json`:

```json
{
  "weather": {
    "last_attempt_ts": 1755450000.0,
    "last_success_ts": 1755450000.0,
    "consecutive_failures": 0,
    "last_error": null
  }
}
```

A run counts as a success only if `generate()` didn't raise *and* the hardware push (when one was needed) returned `True` — a module that runs to completion but fails to actually reach the panel is still recorded as a failure, hash unsaved, so the next scheduled run retries automatically. `!health` in Discord surfaces any module with `consecutive_failures > 0` plus a recent-activity summary; `cat data/health.json` works too.

**Process-level crash recovery** — two scopes:

- *Within* the bot process: `discord_bot.py`'s `auto_refresh` tasks-loop body runs inside a try/except, so a bug in any one tick (a flaky API, a malformed alert-override response) is logged and the loop just retries next minute instead of dying.
- *If the bot process itself* dies (segfault, OOM, an exception that somehow escapes the loop guard), that requires an OS-level supervisor — `systemd/eink-discord-bot.service.example` is a ready-to-copy unit with `Restart=on-failure`. It's not installed automatically; see the README's "Watchdog & health checks" section for the install steps.

---

## Testing

Tests live in `tests/`, one file per module (`test_<module>.py`) — 1,644 tests across all 46 display modules as of the last full-coverage sweep, including `test_health.py` for the watchdog helpers. Conventions:

- Mock external HTTP calls (`unittest.mock.patch`/`MagicMock`) so tests run offline and fast — no real network or hardware access, ever.
- Use `tmp_path` for any file state (caches, output BMPs, `data/health.json`) so tests never touch real project files.
- Cover pure helpers (parsing, formatting, math) *and* the `generate()`/`_render()` entry points end-to-end — a module with only helper-level coverage is considered under-tested, since `generate()` is the code path a real display refresh actually takes.
- Assert the returned path exists and the output is a valid BMP at the expected size for `generate()`-level tests.

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
