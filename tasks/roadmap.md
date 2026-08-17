# E-Ink Display — Feature Roadmap

Backlog of enhancement ideas. Effort is a rough estimate (S = <1hr, M = a few hrs,
L = a day+). "Data source" notes the free API; "no key" means no signup required.

---

## Shipped

- [x] RainViewer high-resolution radar source
- [x] 7-color radar mode (`radar_mode: seven_color`) — uses all Waveshare ink channels
- [x] Radar frame timestamp in panel mode (bottom-left corner)
- [x] Per-module update intervals (`MODULE_INTERVALS` in `utils.py`)
- [x] Web preview route (`/preview`, `/preview/<name>`, `/preview-all`)
- [x] Air Quality Index module (AirNow)
- [x] Countdown timer module (config-driven)
- [x] Sports scores module (ESPN, no key)
- [x] Word of the Day module (Merriam-Webster)
- [x] 48-hour forecast graph module (Open-Meteo, keyless)
- [x] Aurora forecast module (NOAA SWPC, keyless)
- [x] Pollen count module (Open-Meteo, keyless)
- [x] ISS tracker module (wheretheiss.at, keyless)
- [x] Recent earthquakes module (USGS, keyless)
- [x] Stock watchlist module (Yahoo Finance)
- [x] XKCD comic of the day module (keyless)
- [x] Electricity grid carbon intensity module (ElectricityMaps / UK Carbon Intensity API)
- [x] Now playing module (Last.fm)
- [x] Local traffic module (TomTom)
- [x] Calendar / agenda module (iCal/ICS URLs)

---

## Radar / Weather

- [x] **48-hour forecast graph** — Open-Meteo, temp line + precip bars. (S)
- [x] **Aurora forecast** — NOAA SWPC Kp-index, visibility verdict by lat. (M)
- [x] **Severe weather polygon overlay** — NWS alerts GeoJSON over radar. (L)
- [x] **Pollen count** — Open-Meteo air quality, 6 species. (M)
- [ ] **Lightning overlay** — RainViewer lightning tile path, semi-transparent strike
      markers over the radar canvas. (M)
- [ ] **RainViewer animation** — animate last 6–12 frames as a GIF/slideshow.
      Note: each e-ink refresh is slow (~15s on 7-color), so this is a slideshow. (M)

## New Display Modules

- [x] **Calendar / agenda** — iCal/ICS URL parser, today + 7 days, stdlib VEVENT. (M)
- [x] **ISS tracker** — wheretheiss.at, equirectangular world map, haversine dist. (S)
- [x] **Recent earthquakes** — USGS GeoJSON, world map circles + distance list. (M)
- [x] **Stock watchlist** — Yahoo Finance quote API, PIL-drawn triangles. (M)
- [x] **Electricity grid carbon intensity** — ElectricityMaps + UK fallback (keyless). (M)
- [x] **Now playing** — Last.fm recent tracks, album art, track + artist. (M)
- [x] **XKCD / comic of the day** — xkcd.com API, letterboxed art, daily cache. (S)
- [x] **Local traffic** — TomTom Traffic Incidents API, incident cards. (M)

## Infrastructure / Quality of Life

- [ ] **Composite dashboard mode** — tile 2–4 modules on one 800×480 screen
      (e.g. weather + agenda + AQI). Biggest layout upgrade; the module `generate()`
      contract makes it feasible. (L)
- [x] **Scheduled night sleep** — `night_sleep.enabled/start/end` in config; main.py
      and discord_bot auto_refresh skip refreshes in the window. (S)
- [x] **Periodic full-clear** — `full_clear_interval` in config; display.py runs an
      extra Clear() cycle every N pushes to reduce 7-color ACeP ghosting. (S)
- [x] **Alert-driven auto-override** — `alert_auto_override: true` in config; auto_refresh
      polls NWS every 5 min and force-switches to radar on Severe/Extreme alerts,
      then restores the previous module when they clear. (M)
- [x] **Discord `!status`** — now shows last image age, effective refresh interval,
      radar frame age (from rv_frame_state.json), sleep window, and alert override
      status. (M)
- [ ] **Home Assistant / MQTT bridge** — publish current module state; let HA trigger
      module switches. (L)
- [x] **Watchdog + health check** — auto-restart on crash, log last-successful-refresh
      per module. (M)

---

## High signal-per-effort picks

1. **48h forecast graph** — zero new deps, reuses fetched data.
2. **Calendar / agenda** — most useful daily glance; real data via MCP.
3. **Composite dashboard mode** — multiplies the value of every existing module.
